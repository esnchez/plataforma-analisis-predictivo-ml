"""Developer convenience: `churn <command>` runs any service from the venv, without Docker.

Each service also has its own entry point (`python -m churn.<service>`), used by the containers.

    churn seed        create bucket + table, land synthetic data in S3 raw/      (churn.localdev)
    churn train       features -> XGBoost -> new model version in S3              (churn.training)
    churn score       features -> latest model -> DynamoDB + predictions/         (churn.scoring)
    churn backtest    past months' predictions with point-in-time models          (churn.localdev.backtest)
    churn monitor     past predictions vs real outcomes -> monitoring/ report     (churn.monitoring)
    churn serve       start the HTTP API                                          (churn.api)
    churn lookup ID   read one customer's score from DynamoDB
    churn at-risk     list customers in a risk band
    churn run-all     seed -> train -> score -> backtest -> monitor
"""

import argparse
import json

from churn.localdev.args import add_seed_arguments
from churn.shared.config import get_settings


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


# Imports live inside each command so `churn lookup` does not load pandas or XGBoost.
def cmd_seed(settings, args):
    from churn.localdev import seed

    print(seed.run(settings, args.customers, args.months, args.last_month, args.seed))


def cmd_train(settings, args):
    from churn.training import job

    _print(job.run(settings))


def cmd_score(settings, args):
    from churn.scoring import job

    _print(job.run(settings, args.snapshot_month))


def cmd_backtest(settings, args):
    from churn.localdev import backtest

    _print(backtest.run(settings, args.backtest_months))


def cmd_monitor(settings, args):
    from churn.monitoring import job

    _print(job.run(settings))


def cmd_serve(settings, args):
    import uvicorn

    uvicorn.run("churn.api.app:app", host=args.host, port=args.port, workers=args.workers)


def cmd_lookup(settings, args):
    from churn.api.lookup import get_customer_score
    from churn.shared.aws import dynamodb_resource

    item = get_customer_score(dynamodb_resource(settings).Table(settings.table), args.customer_id)
    _print(item if item else {"error": f"No active churn score for {args.customer_id}"})


def cmd_at_risk(settings, args):
    from churn.api.lookup import list_customers_by_risk
    from churn.shared.aws import dynamodb_resource

    _print(list_customers_by_risk(dynamodb_resource(settings).Table(settings.table), args.band, args.limit))


def cmd_run_all(settings, args):
    for step in (cmd_seed, cmd_train, cmd_score, cmd_backtest, cmd_monitor):
        print(f"== {step.__name__.removeprefix('cmd_')}")
        step(settings, args)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="churn", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    add_seed_arguments(p := sub.add_parser("seed"))
    p.set_defaults(func=cmd_seed)
    sub.add_parser("train").set_defaults(func=cmd_train)
    (p := sub.add_parser("score")).add_argument("--snapshot-month", help="YYYY-MM, defaults to the latest")
    p.set_defaults(func=cmd_score)
    p = sub.add_parser("backtest")
    p.add_argument("--months", dest="backtest_months", nargs="+", help="YYYY-MM months (default: all possible)")
    p.set_defaults(func=cmd_backtest)
    sub.add_parser("monitor").set_defaults(func=cmd_monitor)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--workers", type=int, default=1)
    p.set_defaults(func=cmd_serve)
    (p := sub.add_parser("lookup")).add_argument("customer_id")
    p.set_defaults(func=cmd_lookup)
    p = sub.add_parser("at-risk")
    p.add_argument("--band", default="high", choices=["high", "medium", "low"])
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_at_risk)
    add_seed_arguments(p := sub.add_parser("run-all"))
    p.set_defaults(func=cmd_run_all, snapshot_month=None, backtest_months=None)

    args = parser.parse_args(argv)
    args.func(get_settings(), args)


if __name__ == "__main__":
    main()
