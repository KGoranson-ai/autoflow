"""
Create a test license subscription in the database.

Example:
    python3 create_test_license.py --email test@typestra.com --tier pro
"""

from __future__ import annotations

import argparse
import logging
import sys

from dotenv import load_dotenv
from sqlalchemy.orm import sessionmaker

from database import create_engine_from_env, init_db
from license import create_subscription

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a test user subscription and print the generated license key."
    )
    parser.add_argument(
        "--email",
        default="test@typestra.com",
        help="Email address for the test user (default: test@typestra.com).",
    )
    parser.add_argument(
        "--tier",
        default="pro",
        choices=("basic", "pro", "premium"),
        help="Subscription tier (default: pro).",
    )
    return parser.parse_args()


def main() -> int:
    load_dotenv()
    args = _parse_args()

    try:
        engine = create_engine_from_env()
        init_db(engine)
        Session = sessionmaker(bind=engine)
    except Exception as e:
        logger.error("Database setup failed: %s", e)
        print(f"Error: failed to connect/setup database: {e}", file=sys.stderr)
        return 1

    session = Session()
    try:
        result = create_subscription(
            session=session,
            email=args.email,
            tier=args.tier,
        )
        session.commit()
    except Exception as e:
        session.rollback()
        logger.error("Failed to create test license: %s", e)
        print(f"Error: failed to create test license: {e}", file=sys.stderr)
        return 1
    finally:
        session.close()

    license_key = result["license_key"]
    print("")
    print("Test license created successfully")
    print("--------------------------------")
    print(f"Email:       {args.email}")
    print(f"Tier:        {args.tier}")
    print(f"License Key: {license_key}")
    print("")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
