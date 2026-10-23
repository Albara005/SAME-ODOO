"""Print the API's OpenAPI document (used to generate the frontend's typed client).

    python -m scripts.export_openapi > ../../apps/web/openapi.json
"""

from __future__ import annotations

import json
import tempfile

from novara.config import Settings
from novara.main import create_app


def main() -> None:
    tmp = tempfile.mkdtemp()
    app = create_app(Settings(env="development", database_url=f"sqlite:///{tmp}/openapi.db",
                              jwt_secret="openapi-export-only-secret-0123456789abcdef", storage_root=f"{tmp}/s"),
                     create_schema=False)
    print(json.dumps(app.openapi(), indent=1, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()
