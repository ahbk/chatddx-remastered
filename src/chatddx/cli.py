import argparse
from collections.abc import Sequence

import psycopg

from chatddx.core import settings
from chatddx.store.migrate import TOP_TIER, migrate, pending


def _migrate(args: argparse.Namespace) -> None:
    tier = int(args.tier)
    with psycopg.connect(settings.database(admin=True)) as conn:
        if args.dry_run:
            names = [m.name for m in pending(conn, tier)]
        else:
            names = migrate(conn, tier)
    verb = "would apply" if args.dry_run else "applied"
    for name in names:
        print(f"{verb} {name}")
    if not names:
        print(f"nothing to apply up to tier {tier}")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="chatddx")
    commands = parser.add_subparsers(required=True)

    m = commands.add_parser("migrate", help="apply database migrations as DB_ADMIN")
    _ = m.add_argument(
        "--tier",
        type=int,
        choices=range(TOP_TIER + 1),
        default=TOP_TIER,
        help=f"apply migrations up to this tier (default {TOP_TIER})",
    )
    _ = m.add_argument(
        "--dry-run", action="store_true", help="list what would be applied"
    )
    m.set_defaults(run=_migrate)

    args = parser.parse_args(argv)
    args.run(args)
