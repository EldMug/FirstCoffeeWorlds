"""The module and installed console command share the same CLI."""

if __name__ == "__main__":
    from .cli import main

    raise SystemExit(main())
