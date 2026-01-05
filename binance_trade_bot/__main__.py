import argparse
from .crypto_trading import main

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Binance Trade Bot')
    parser.add_argument('-y', '--yes', action='store_true', help='Skip confirmation prompt')
    args = parser.parse_args()

    try:
        main(skip_confirm=args.yes)
    except KeyboardInterrupt:
        pass
