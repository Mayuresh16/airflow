# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

#
from __future__ import annotations

from typing import Any
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

from airflow.models import Connection
from airflow.providers.common.sql.hooks.handlers import fetch_all_handler
from airflow.providers.exasol.hooks.exasol import ExasolHook

TASK_ID = "sql-operator"
HOST = "host"
DEFAULT_CONN_ID = "exasol_default"
PASSWORD = "password"


class ExasolHookForTests(ExasolHook):
    conn_name_attr = "exasol_conn_id"
    get_conn = MagicMock(name="conn")


@pytest.fixture(autouse=True)
def create_connection(create_connection_without_db):
    create_connection_without_db(
        Connection(
            conn_id=DEFAULT_CONN_ID,
            conn_type="exasol",
            host=HOST,
            login=None,
            password=PASSWORD,
            extra=None,
        )
    )


@pytest.fixture
def exasol_hook():
    return ExasolHook()


def get_columns(fields: list[str]) -> dict[str, dict[str, Any]]:
    return {
        field: {"type": "VARCHAR", "nullable": True, "precision": None, "scale": None, "length": None}
        for field in fields
    }


index = 0


@pytest.mark.parametrize(
    "return_last, split_statements, sql, cursor_calls,"
    "cursor_descriptions, cursor_results, hook_descriptions, hook_results, ",
    [
        pytest.param(
            True,
            False,
            "select * from test.test",
            ["select * from test.test"],
            [["id", "value"]],
            ([[1, 2], [11, 12]],),
            [
                [
                    ("id", "VARCHAR", None, None, None, None, True),
                    ("value", "VARCHAR", None, None, None, None, True),
                ]
            ],
            [[1, 2], [11, 12]],
            id="The return_last set and no split statements set on single query in string",
        ),
        pytest.param(
            False,
            False,
            "select * from test.test;",
            ["select * from test.test;"],
            [["id", "value"]],
            ([[1, 2], [11, 12]],),
            [
                [
                    ("id", "VARCHAR", None, None, None, None, True),
                    ("value", "VARCHAR", None, None, None, None, True),
                ]
            ],
            [[1, 2], [11, 12]],
            id="The return_last not set and no split statements set on single query in string",
        ),
        pytest.param(
            True,
            True,
            "select * from test.test;",
            ["select * from test.test;"],
            [["id", "value"]],
            ([[1, 2], [11, 12]],),
            [
                [
                    ("id", "VARCHAR", None, None, None, None, True),
                    ("value", "VARCHAR", None, None, None, None, True),
                ]
            ],
            [[1, 2], [11, 12]],
            id="The return_last set and split statements set on single query in string",
        ),
        pytest.param(
            False,
            True,
            "select * from test.test;",
            ["select * from test.test;"],
            [["id", "value"]],
            ([[1, 2], [11, 12]],),
            [
                [
                    ("id", "VARCHAR", None, None, None, None, True),
                    ("value", "VARCHAR", None, None, None, None, True),
                ]
            ],
            [[[1, 2], [11, 12]]],
            id="The return_last not set and split statements set on single query in string",
        ),
        pytest.param(
            True,
            True,
            "select * from test.test;select * from test.test2;",
            ["select * from test.test;", "select * from test.test2;"],
            [["id", "value"], ["id2", "value2"]],
            ([[1, 2], [11, 12]], [[3, 4], [13, 14]]),
            [
                [
                    ("id2", "VARCHAR", None, None, None, None, True),
                    ("value2", "VARCHAR", None, None, None, None, True),
                ]
            ],
            [[3, 4], [13, 14]],
            id="The return_last set and split statements set on multiple queries in string",
        ),  # Failing
        pytest.param(
            False,
            True,
            "select * from test.test;select * from test.test2;",
            ["select * from test.test;", "select * from test.test2;"],
            [["id", "value"], ["id2", "value2"]],
            ([[1, 2], [11, 12]], [[3, 4], [13, 14]]),
            [
                [
                    ("id", "VARCHAR", None, None, None, None, True),
                    ("value", "VARCHAR", None, None, None, None, True),
                ],
                [
                    ("id2", "VARCHAR", None, None, None, None, True),
                    ("value2", "VARCHAR", None, None, None, None, True),
                ],
            ],
            [[[1, 2], [11, 12]], [[3, 4], [13, 14]]],
            id="The return_last not set and split statements set on multiple queries in string",
        ),
        pytest.param(
            True,
            True,
            ["select * from test.test;"],
            ["select * from test.test"],
            [["id", "value"]],
            ([[1, 2], [11, 12]],),
            [
                [
                    ("id", "VARCHAR", None, None, None, None, True),
                    ("value", "VARCHAR", None, None, None, None, True),
                ]
            ],
            [[[1, 2], [11, 12]]],
            id="The return_last set on single query in list",
        ),
        pytest.param(
            False,
            True,
            ["select * from test.test;"],
            ["select * from test.test"],
            [["id", "value"]],
            ([[1, 2], [11, 12]],),
            [
                [
                    ("id", "VARCHAR", None, None, None, None, True),
                    ("value", "VARCHAR", None, None, None, None, True),
                ]
            ],
            [[[1, 2], [11, 12]]],
            id="The return_last not set on single query in list",
        ),
        pytest.param(
            True,
            True,
            "select * from test.test;select * from test.test2;",
            ["select * from test.test", "select * from test.test2"],
            [["id", "value"], ["id2", "value2"]],
            ([[1, 2], [11, 12]], [[3, 4], [13, 14]]),
            [
                [
                    ("id2", "VARCHAR", None, None, None, None, True),
                    ("value2", "VARCHAR", None, None, None, None, True),
                ]
            ],
            [[3, 4], [13, 14]],
            id="The return_last set on multiple queries in list",
        ),
        pytest.param(
            False,
            True,
            "select * from test.test;select * from test.test2;",
            ["select * from test.test", "select * from test.test2"],
            [["id", "value"], ["id2", "value2"]],
            ([[1, 2], [11, 12]], [[3, 4], [13, 14]]),
            [
                [
                    ("id", "VARCHAR", None, None, None, None, True),
                    ("value", "VARCHAR", None, None, None, None, True),
                ],
                [
                    ("id2", "VARCHAR", None, None, None, None, True),
                    ("value2", "VARCHAR", None, None, None, None, True),
                ],
            ],
            [[[1, 2], [11, 12]], [[3, 4], [13, 14]]],
            id="The return_last not set on multiple queries not set",
        ),
    ],
)
def test_query(
    exasol_hook,
    return_last,
    split_statements,
    sql,
    cursor_calls,
    cursor_descriptions,
    cursor_results,
    hook_descriptions,
    hook_results,
):
    with patch("airflow.providers.exasol.hooks.exasol.ExasolHook.get_conn") as mock_conn:
        cursors = []
        for index in range(len(cursor_descriptions)):
            cur = mock.MagicMock(
                rowcount=lambda: len(cursor_results[index]),
            )
            cur.columns.return_value = get_columns(cursor_descriptions[index])
            cur.fetchall.return_value = cursor_results[index]
            cursors.append(cur)
        mock_conn.execute.side_effect = cursors
        mock_conn.return_value = mock_conn
        results = exasol_hook.run(
            sql=sql, handler=fetch_all_handler, return_last=return_last, split_statements=split_statements
        )

        assert exasol_hook.descriptions == hook_descriptions
        assert exasol_hook.last_description == hook_descriptions[-1]
        assert results == hook_results
        cur.close.assert_called()


@pytest.mark.parametrize(
    "empty_statement",
    [
        pytest.param([], id="Empty list"),
        pytest.param("", id="Empty string"),
        pytest.param("\n", id="Only EOL"),
    ],
)
def test_no_query(empty_statement):
    dbapi_hook = ExasolHookForTests()
    dbapi_hook.get_conn.return_value.cursor.rowcount = lambda: 0
    with pytest.raises(ValueError) as err:
        dbapi_hook.run(sql=empty_statement)
    assert err.value.args[0] == "List of SQL statements is empty"
