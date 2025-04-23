from web3 import Web3
import time
import random
from pathlib import Path

from loguru import logger
import sys

from concurrent.futures import ThreadPoolExecutor, as_completed

logger.remove()
logger.add(sys.stdout, format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | {message}")

from inputs.config import config
from data.data import data

class Zora:
    def __init__(self, key, deposit_address) -> None:
        self.web3 = Web3(Web3.HTTPProvider(config['RPC']))
        self.zora_contract = self.web3.eth.contract(self.web3.to_checksum_address(data['ZORA']['contract']), abi=data['ZORA']['abi'])
        self.claim_contract = self.web3.eth.contract(self.web3.to_checksum_address(data['CLAIM']['contract']), abi=data['CLAIM']['abi'])
        self.decimals = 18
        
        self.allocation = 0

        self.privatekey = key
        self.account = self.web3.eth.account.from_key(self.privatekey)
        self.address = self.account.address

        self.deposit_address = self.web3.to_checksum_address(deposit_address)
    
    def wait_claim_open(self):
        while True:
            open_status = self.claim_contract.functions.claimIsOpen().call()
            if open_status:
                return True
            else:
                logger.info(f'{self.address} | Claim is not open yet')
            time.sleep(1)

    def claim_without_signature(self):
        for _ in range(config['RETRY_COUNT']):
            gasPrice = self.web3.eth.gas_price
            transaction = self.claim_contract.functions.claim(self.address).build_transaction({
                'from': self.address,
                'value': 0,
                'gasPrice': int(gasPrice * config['GAS_MULTIPLIER']),
                #'maxFeePerGas': int(gasPrice * config['GAS_MULTIPLIER']),
                #'maxPriorityFeePerGas': int(gasPrice / 1000),
                'nonce': self.web3.eth.get_transaction_count(self.address),
                'chainId': self.web3.eth.chain_id,
            })
            transaction['gas'] = int(self.web3.eth.estimate_gas(transaction) * random.uniform(1.4, 1.5))
            
            signed_transaction = self.account.sign_transaction(transaction)
            txn_hash = self.web3.eth.send_raw_transaction(signed_transaction.rawTransaction)
            txn_receipt = self.web3.eth.wait_for_transaction_receipt(txn_hash, timeout=10)
            if txn_receipt['status'] == 1:
                logger.success(f'[CLAIM] | {self.address} | https://basescan.org/tx/{(self.web3.to_hex(txn_hash))}')
                return True
            else:
                logger.error(f'{self.address} | Claim tx failed')

    def send_zora(self):
        for _ in range(config['RETRY_COUNT']):
            if self.allocation != 0:
                balance = self.allocation
            else:
                balance = self.check_zora_balance()
            if balance > 0:
                logger.info(f'{self.address}: Got {balance / 10 ** self.decimals} $ZORA, sending.')
                
                gasPrice = self.web3.eth.gas_price
                transaction = self.zora_contract.functions.transfer(self.deposit_address, balance).build_transaction({
                    'from': self.address,
                    'value': 0,
                    'gasPrice': int(gasPrice * config['GAS_MULTIPLIER']),
                    #'maxFeePerGas': int(gasPrice * config['GAS_MULTIPLIER']),
                    #'maxPriorityFeePerGas': int(gasPrice / 1000),
                    'nonce': self.web3.eth.get_transaction_count(self.address),
                    'chainId': self.web3.eth.chain_id,
                })
                transaction['gas'] = int(self.web3.eth.estimate_gas(transaction) * 1.5)
                
                signed_transaction = self.account.sign_transaction(transaction)
                txn_hash = self.web3.eth.send_raw_transaction(signed_transaction.rawTransaction)
                txn_receipt = self.web3.eth.wait_for_transaction_receipt(txn_hash, timeout=10)
                if txn_receipt['status'] == 1:
                    logger.success(f'[SEND] | {self.address} | https://basescan.org/tx/{(self.web3.to_hex(txn_hash))}')
                    return True
                else:
                    logger.error(f'{self.address} | Send tx failed')
            else:
                logger.info(f'{self.address}: No $ZORA on wallet')

    def check_zora_balance(self):
        balance = self.zora_contract.functions.balanceOf(self.address).call()
        return balance

    def check_if_need_claim(self):
        claim = self.claim_contract.functions.accountClaim(self.address).call()
        allocation = claim[0]
        claimed = claim[1] # True or False
        
        if not claimed:
            result = round(allocation / 10 ** self.decimals, 2)
            
            if result > 0:
                logger.success(f'{self.address}: Have {result} $ZORA not claimed yet')
                return True
            else:
                logger.error(f'{self.address}: not eligible')
                return False
        else:
            logger.success(f'{self.address}: Already claimed')
            return False
            
def check_and_create_dir(file_path: Path):
    if file_path.is_file():
        pass
    else:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.touch()
    return None

def main(key, deposit_address):
    try:
        zora = Zora(key, deposit_address)
        zora.wait_claim_open()
        need_claim = zora.check_if_need_claim()

        if need_claim:
            zora.claim_without_signature()
        
        zora.send_zora()

    except Exception as error:
        logger.error(f'{zora.address} | Error: {error}')
        with open('results/failed.txt', 'a') as file:
            file.write(f'{key};{deposit_address}\n')

def process_accs(accounts_list):
    def worker(account):
        if len(account.split(';')) == 2:
            key, deposit_address = account.split(';')
        else:
            logger.error('Wrong format wallets.txt. Should be key;deposit_address format!')
            return

        main(key, deposit_address)

        to_sleep = random.uniform(*config['DELAY_ACCS'])
        logger.info(f'Sleep {round(to_sleep, 2)} sec')
        time.sleep(to_sleep)

    with ThreadPoolExecutor(max_workers=config['THREADS']) as executor:
        futures = [executor.submit(worker, acc) for acc in accounts_list]
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as e:
                logger.error(f"Thread raised an exception: {e}")

if __name__ == '__main__':
    #check_and_create_dir(Path('./results/success.txt'))
    check_and_create_dir(Path('./results/failed.txt'))

    with open('inputs/wallets.txt', 'r') as file:
        wallets = file.read().splitlines()

    #with open('inputs/proxies.txt', 'r') as file:
    #    proxies = file.read().splitlines()

    if config['TO_SHUFFLE']:
        random.shuffle(wallets)

    process_accs(wallets)