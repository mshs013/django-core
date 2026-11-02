"""Query builder: users pick a root model, join related models, choose columns (with functions such as DATE / HOUR / text
before ','), filter (=, ≠, IN, NOT IN, BETWEEN, last N days ...), group and aggregate (COUNT, COUNT DISTINCT, SUM, AVG, MIN, MAX,
conditional "only where" aggregates, CASE columns), filter the grouped result (HAVING), sort, chart, save, share and export.

Everything compiles to the Django ORM (no raw SQL); see ``engine.py`` for the definition format and ``registry.py`` for which
models can be used. Guide: ``docs/QUERY_BUILDER.md``."""
from .catalogue import QueryError, catalogue, resolve  # noqa: F401
from .engine import (AGGREGATES, CHART_TYPES, FUNCTIONS, HAVING_OPERATORS, OPERATOR_LABELS, OPERATORS, QuerySpec,  # noqa: F401
                     build, to_json, validate)
from .registry import (QueryModel, all_query_models, allowed_for, get_query_model, query_model_for,  # noqa: F401
                       register_query_model, unregister_query_model)
from .service import chart_data, display, export_bytes, preview  # noqa: F401
