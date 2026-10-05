import argparse
import getpass
from collections.abc import Sequence
from pathlib import Path

import psycopg

from chatddx.core import settings
from chatddx.core.rig import rig
from chatddx.facts.facts import Facts
from chatddx.fake_vllm.server import run
from chatddx.identity import Role
from chatddx.inventory.inventory import Inventory
from chatddx.inventory.sources import DirectorySource
from chatddx.seed import load_cases, plan_factors, seed
from chatddx.seed.plan import SAMPLE
from chatddx.seed.world import endpoints
from chatddx.store.migrate import TOP_TIER, migrate, pending
from chatddx.store.people import People


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


def _person_add(args: argparse.Namespace) -> None:
    with psycopg.connect(settings.database()) as conn:
        person = People(conn).add(args.login, args.name, map(Role, args.role))
    print(f"added {person.login} (id {person.id})")


def _person_password(args: argparse.Namespace) -> None:
    password = getpass.getpass(f"new password for {args.login}: ")
    if getpass.getpass("again: ") != password:
        raise SystemExit("passwords don't match")
    with psycopg.connect(settings.database()) as conn:
        people = People(conn)
        person = people.find(args.login)
        if person is None:
            raise SystemExit(f"no person with login {args.login!r}")
        people.set_password(person.id, password)
    print(f"password set for {args.login}")


def _init_data(args: argparse.Namespace) -> None:
    sample: Path = args.data
    facts = Facts.load(*(args.facts or [sample / "facts.toml"]))
    plan = plan_factors(sample / "factors.toml", facts, rig())
    cases = load_cases(sample / "cases.toml")
    world = None if args.world is None else Inventory.load(args.world)
    if world is not None:
        source = world.source(args.source)
    elif args.vignettes.is_dir():
        source = DirectorySource(name=args.source, path=args.vignettes.resolve())
    else:
        raise SystemExit(
            f"--vignettes {args.vignettes}: give a directory of <id>.txt files, such as "
            + "sample-world/vignettes"
        )
    if not set(cases) & set(source.ids()):
        raise SystemExit(
            f"none of the sample's {len(cases)} cases is in source {args.source!r}; "
            + "the sample World has them: --world sample-world/inventory.toml"
        )
    with psycopg.connect(settings.database()) as conn:
        user = People(conn).find(args.user)
        if user is None:
            raise SystemExit(
                f"no person with login {args.user!r}: add them with `chatddx person add`"
            )
        lines = seed(conn, plan, cases, source, user, giftbag=args.giftbag)
    if world is not None:
        lines += endpoints(world, plan)
    for line in lines:
        print(line)


def _fake_vllm(args: argparse.Namespace) -> None:
    run(args.model, args.argv, delay=args.delay, runaway=args.runaway)


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

    person = commands.add_parser("person", help="manage people as DB_USER")
    person_commands = person.add_subparsers(required=True)
    add = person_commands.add_parser("add", help="add a person")
    _ = add.add_argument("login")
    _ = add.add_argument("name")
    _ = add.add_argument(
        "--role", action="append", default=[], choices=[r.value for r in Role]
    )
    add.set_defaults(run=_person_add)
    password = person_commands.add_parser("password", help="set a person's password")
    _ = password.add_argument("login")
    password.set_defaults(run=_person_password)

    init = commands.add_parser(
        "init-data",
        help="seed the sample data for the archive and share it with a person",
    )
    _ = init.add_argument("user", help="the login to share the archive with")
    vignettes = init.add_mutually_exclusive_group(required=True)
    _ = vignettes.add_argument(
        "--world",
        type=Path,
        help="a World inventory whose [source.<name>] table locates the vignettes",
    )
    _ = vignettes.add_argument(
        "--vignettes",
        type=Path,
        help="a directory of <id>.txt vignettes, in place of a World inventory",
    )
    _ = init.add_argument(
        "--source",
        default="sample",
        help="the vignette source's name, in the World inventory and the cases (sample)",
    )
    _ = init.add_argument(
        "--giftbag", action="store_true", help="also give the user forks of their own"
    )
    _ = init.add_argument(
        "--data",
        type=Path,
        default=SAMPLE,
        help="what to seed: a directory with factors.toml, cases.toml and facts.toml "
        + "(the sample data in the package)",
    )
    _ = init.add_argument(
        "--facts",
        type=Path,
        action="append",
        help="model facts to write per-model chunks from (the sample's facts.toml)",
    )
    init.set_defaults(run=_init_data)

    fake = commands.add_parser(
        "fake-vllm",
        help="serve a fake vLLM 0.24 for a model, set up by vllm serve's own flags",
    )
    _ = fake.add_argument(
        "--delay", type=float, default=0.03, help="seconds between streamed tokens"
    )
    _ = fake.add_argument(
        "--runaway",
        action="store_true",
        help="go on with newlines till max_tokens or the context runs out, after a "
        + "text answer or before a document's closing brace, as gpt-oss does at times",
    )
    _ = fake.add_argument("model", help="the model, as vllm serve takes it")
    _ = fake.add_argument(
        "argv",
        nargs=argparse.REMAINDER,
        help="vllm serve's flags, such as --served-model-name, --reasoning-parser, "
        + "--enable-auto-tool-choice, --tool-call-parser, --host and --port",
    )
    fake.set_defaults(run=_fake_vllm)

    args = parser.parse_args(argv)
    args.run(args)
