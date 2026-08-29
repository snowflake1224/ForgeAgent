# Calculator bug fixture

This benchmark contains an intentional defect: `src/calc.py::add` subtracts
its arguments. The test suite specifies the expected addition behavior.

The fixture is copied into a fresh git workspace for every architecture run.
