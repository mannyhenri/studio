Engines for the AI tools' host tests (tests/unit/test_generators.py): manifests in Film Engine's generator.json format
and two stand-in engines that speak the real engines' command line and progress file (ported from Film Engine's
backend/tests/fixtures/generators). FAKE_MODE=fail makes fake_engine.py fail, FAKE_MODE=slow keeps it running until it's
stopped (for Cancel).
