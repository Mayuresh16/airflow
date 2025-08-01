#
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
"""Unit tests for stringified DAGs."""

from __future__ import annotations

import copy
import dataclasses
import importlib
import importlib.util
import json
import logging
import logging.config
import multiprocessing
import os
import pickle
import re
import sys
import warnings
from collections.abc import Generator
from datetime import datetime, timedelta, timezone as dt_timezone
from glob import glob
from pathlib import Path
from textwrap import dedent
from typing import TYPE_CHECKING
from unittest import mock

import attrs
import pendulum
import pytest
from dateutil.relativedelta import FR, relativedelta
from kubernetes.client import models as k8s

import airflow
from airflow.config_templates.airflow_local_settings import DEFAULT_LOGGING_CONFIG
from airflow.exceptions import (
    AirflowException,
    ParamValidationError,
    SerializationError,
)
from airflow.models.asset import AssetModel
from airflow.models.connection import Connection
from airflow.models.dag import DAG
from airflow.models.dagbag import DagBag
from airflow.models.mappedoperator import MappedOperator
from airflow.models.xcom import XCOM_RETURN_KEY, XComModel
from airflow.providers.cncf.kubernetes.pod_generator import PodGenerator
from airflow.providers.standard.operators.bash import BashOperator
from airflow.providers.standard.sensors.bash import BashSensor
from airflow.sdk import AssetAlias, BaseHook, teardown
from airflow.sdk.bases.decorator import DecoratedOperator
from airflow.sdk.bases.operator import BaseOperator
from airflow.sdk.definitions._internal.expandinput import EXPAND_INPUT_EMPTY
from airflow.sdk.definitions.asset import Asset, AssetUniqueKey
from airflow.sdk.definitions.param import Param, ParamsDict
from airflow.sdk.definitions.taskgroup import TaskGroup
from airflow.security import permissions
from airflow.serialization.enums import Encoding
from airflow.serialization.json_schema import load_dag_schema_dict
from airflow.serialization.serialized_objects import (
    BaseSerialization,
    SerializedBaseOperator,
    SerializedDAG,
    XComOperatorLink,
)
from airflow.task.priority_strategy import _DownstreamPriorityWeightStrategy
from airflow.ti_deps.deps.ready_to_reschedule import ReadyToRescheduleDep
from airflow.timetables.simple import NullTimetable, OnceTimetable
from airflow.triggers.base import StartTriggerArgs
from airflow.utils import timezone
from airflow.utils.module_loading import qualname
from airflow.utils.operator_resources import Resources

from tests_common.test_utils.config import conf_vars
from tests_common.test_utils.markers import skip_if_force_lowest_dependencies_marker
from tests_common.test_utils.mock_operators import (
    AirflowLink,
    AirflowLink2,
    CustomOperator,
    GithubLink,
    MockOperator,
)
from tests_common.test_utils.timetables import (
    CustomSerializationTimetable,
    cron_timetable,
    delta_timetable,
)

if TYPE_CHECKING:
    from airflow.sdk.definitions.context import Context

AIRFLOW_REPO_ROOT_PATH = Path(airflow.__file__).parents[3]


executor_config_pod = k8s.V1Pod(
    metadata=k8s.V1ObjectMeta(name="my-name"),
    spec=k8s.V1PodSpec(
        containers=[
            k8s.V1Container(
                name="base",
                volume_mounts=[k8s.V1VolumeMount(name="my-vol", mount_path="/vol/")],
            )
        ]
    ),
)
TYPE = Encoding.TYPE
VAR = Encoding.VAR
serialized_simple_dag_ground_truth = {
    "__version": 2,
    "dag": {
        "default_args": {
            "__type": "dict",
            "__var": {
                "depends_on_past": False,
                "retries": 1,
                "retry_delay": {"__type": "timedelta", "__var": 300.0},
                "max_retry_delay": {"__type": "timedelta", "__var": 600.0},
            },
        },
        "start_date": 1564617600.0,
        "timetable": {
            "__type": "airflow.timetables.interval.DeltaDataIntervalTimetable",
            "__var": {
                "delta": 86400.0,
            },
        },
        "task_group": {
            "_group_id": None,
            "group_display_name": "",
            "prefix_group_id": True,
            "children": {
                "bash_task": ("operator", "bash_task"),
                "custom_task": ("operator", "custom_task"),
            },
            "tooltip": "",
            "ui_color": "CornflowerBlue",
            "ui_fgcolor": "#000",
            "upstream_group_ids": [],
            "downstream_group_ids": [],
            "upstream_task_ids": [],
            "downstream_task_ids": [],
        },
        "is_paused_upon_creation": False,
        "dag_id": "simple_dag",
        "deadline": None,
        "catchup": False,
        "disable_bundle_versioning": False,
        "doc_md": "### DAG Tutorial Documentation",
        "fileloc": None,
        "_processor_dags_folder": (
            AIRFLOW_REPO_ROOT_PATH / "airflow-core" / "tests" / "unit" / "dags"
        ).as_posix(),
        "tasks": [
            {
                "__type": "operator",
                "__var": {
                    "task_id": "bash_task",
                    "retries": 1,
                    "retry_delay": 300.0,
                    "max_retry_delay": 600.0,
                    "downstream_task_ids": [],
                    "ui_color": "#f0ede4",
                    "ui_fgcolor": "#000",
                    "template_ext": [".sh", ".bash"],
                    "template_fields": ["bash_command", "env", "cwd"],
                    "template_fields_renderers": {
                        "bash_command": "bash",
                        "env": "json",
                    },
                    "bash_command": "echo {{ task.task_id }}",
                    "task_type": "BashOperator",
                    "_task_module": "airflow.providers.standard.operators.bash",
                    "owner": "airflow",
                    "pool": "default_pool",
                    "is_setup": False,
                    "is_teardown": False,
                    "on_failure_fail_dagrun": False,
                    "executor_config": {
                        "__type": "dict",
                        "__var": {
                            "pod_override": {
                                "__type": "k8s.V1Pod",
                                "__var": PodGenerator.serialize_pod(executor_config_pod),
                            }
                        },
                    },
                    "doc_md": "### Task Tutorial Documentation",
                    "_needs_expansion": False,
                    "weight_rule": "downstream",
                    "start_trigger_args": None,
                    "start_from_trigger": False,
                    "inlets": [
                        {
                            "__type": "asset",
                            "__var": {
                                "extra": {},
                                "group": "asset",
                                "name": "asset-1",
                                "uri": "asset-1",
                            },
                        },
                        {
                            "__type": "asset_alias",
                            "__var": {"group": "asset", "name": "alias-name"},
                        },
                    ],
                    "outlets": [
                        {
                            "__type": "asset",
                            "__var": {
                                "extra": {},
                                "group": "asset",
                                "name": "asset-2",
                                "uri": "asset-2",
                            },
                        },
                    ],
                },
            },
            {
                "__type": "operator",
                "__var": {
                    "task_id": "custom_task",
                    "retries": 1,
                    "retry_delay": 300.0,
                    "max_retry_delay": 600.0,
                    "downstream_task_ids": [],
                    "_operator_extra_links": {"Google Custom": "_link_CustomOpLink"},
                    "ui_color": "#fff",
                    "ui_fgcolor": "#000",
                    "template_ext": [],
                    "template_fields": ["bash_command"],
                    "template_fields_renderers": {},
                    "task_type": "CustomOperator",
                    "_operator_name": "@custom",
                    "_task_module": "tests_common.test_utils.mock_operators",
                    "pool": "default_pool",
                    "is_setup": False,
                    "is_teardown": False,
                    "on_failure_fail_dagrun": False,
                    "_needs_expansion": False,
                    "weight_rule": "downstream",
                    "start_trigger_args": None,
                    "start_from_trigger": False,
                },
            },
        ],
        "timezone": "UTC",
        "access_control": {
            "__type": "dict",
            "__var": {
                "test_role": {
                    "__type": "dict",
                    "__var": {
                        "DAGs": {
                            "__type": "set",
                            "__var": [
                                permissions.ACTION_CAN_READ,
                                permissions.ACTION_CAN_EDIT,
                            ],
                        }
                    },
                }
            },
        },
        "edge_info": {},
        "dag_dependencies": [
            {
                "dependency_id": '{"name": "asset-2", "uri": "asset-2"}',
                "dependency_type": "asset",
                "label": "asset-2",
                "source": "simple_dag",
                "target": "asset",
            },
        ],
        "params": [],
        "tags": [],
    },
}

CUSTOM_TIMETABLE_SERIALIZED = {
    "__type": "tests_common.test_utils.timetables.CustomSerializationTimetable",
    "__var": {"value": "foo"},
}


@pytest.fixture
def testing_assets(session):
    from tests_common.test_utils.db import clear_db_assets

    assets = [Asset(name=f"asset{i}", uri=f"test://asset{i}/") for i in range(1, 5)]

    session.add_all([AssetModel(id=i, name=f"asset{i}", uri=f"test://asset{i}/") for i in range(1, 5)])
    session.commit()

    yield assets

    clear_db_assets()


def make_simple_dag():
    """Make very simple DAG to verify serialization result."""
    with DAG(
        dag_id="simple_dag",
        schedule=timedelta(days=1),
        default_args={
            "retries": 1,
            "retry_delay": timedelta(minutes=5),
            "max_retry_delay": timedelta(minutes=10),
            "depends_on_past": False,
        },
        start_date=datetime(2019, 8, 1),
        is_paused_upon_creation=False,
        access_control={"test_role": {permissions.ACTION_CAN_READ, permissions.ACTION_CAN_EDIT}},
        doc_md="### DAG Tutorial Documentation",
    ) as dag:
        CustomOperator(task_id="custom_task")
        BashOperator(
            task_id="bash_task",
            bash_command="echo {{ task.task_id }}",
            owner="airflow",
            executor_config={"pod_override": executor_config_pod},
            doc_md="### Task Tutorial Documentation",
            inlets=[Asset("asset-1"), AssetAlias(name="alias-name")],
            outlets=Asset("asset-2"),
        )
    return dag


def make_user_defined_macro_filter_dag():
    """
    Make DAGs with user defined macros and filters using locally defined methods.

    For Webserver, we do not include ``user_defined_macros`` & ``user_defined_filters``.

    The examples here test:
        (1) functions can be successfully displayed on UI;
        (2) templates with function macros have been rendered before serialization.
    """

    def compute_last_dagrun(dag: DAG):
        return dag.get_last_dagrun(include_manually_triggered=True)

    default_args = {"start_date": datetime(2019, 7, 10)}
    dag = DAG(
        "user_defined_macro_filter_dag",
        schedule=None,
        default_args=default_args,
        user_defined_macros={
            "last_dagrun": compute_last_dagrun,
        },
        user_defined_filters={"hello": lambda name: f"Hello {name}"},
        catchup=False,
    )
    BashOperator(
        task_id="echo",
        bash_command='echo "{{ last_dagrun(dag) }}"',
        dag=dag,
    )
    return {dag.dag_id: dag}


def get_excluded_patterns() -> Generator[str, None, None]:
    python_version = f"{sys.version_info.major}.{sys.version_info.minor}"
    all_providers = json.loads(
        (AIRFLOW_REPO_ROOT_PATH / "generated" / "provider_dependencies.json").read_text()
    )
    for provider, provider_info in all_providers.items():
        if python_version in provider_info.get("excluded-python-versions"):
            provider_path = provider.replace(".", "/")
            yield f"providers/{provider_path}"
    current_python_version = sys.version_info[:2]
    if current_python_version >= (3, 13):
        # We should remove google when ray is fixed to work with Python 3.13
        # and yandex when it is fixed to work with Python 3.13
        yield "providers/google/tests/system/google/"
        yield "providers/yandex/tests/system/yandex/"


def collect_dags(dag_folder=None):
    """Collects DAGs to test."""
    dags = {}
    import_errors = {}
    dags.update({"simple_dag": make_simple_dag()})
    dags.update(make_user_defined_macro_filter_dag())

    if dag_folder is None:
        patterns = [
            "airflow-core/src/airflow/example_dags",
            # For now include amazon directly because they have many dags and are all serializing without error
            "providers/amazon/tests/system/*/*/",
            "providers/*/tests/system/*/",
            "providers/*/*/tests/system/*/*/",
        ]
    else:
        if isinstance(dag_folder, (list, tuple)):
            patterns = dag_folder
        else:
            patterns = [dag_folder]
    excluded_patterns = [
        f"{AIRFLOW_REPO_ROOT_PATH}/{excluded_pattern}" for excluded_pattern in get_excluded_patterns()
    ]
    for pattern in patterns:
        for directory in glob(f"{AIRFLOW_REPO_ROOT_PATH}/{pattern}"):
            if any([directory.startswith(excluded_pattern) for excluded_pattern in excluded_patterns]):
                continue
            dagbag = DagBag(directory, include_examples=False)
            dags.update(dagbag.dags)
            import_errors.update(dagbag.import_errors)
    return dags, import_errors


def get_timetable_based_simple_dag(timetable):
    """Create a simple_dag variant that uses a timetable."""
    dag = make_simple_dag()
    dag.timetable = timetable
    return dag


def serialize_subprocess(queue, dag_folder):
    """Validate pickle in a subprocess."""
    dags, _ = collect_dags(dag_folder)
    for dag in dags.values():
        queue.put(SerializedDAG.to_json(dag))
    queue.put(None)


@pytest.fixture
def timetable_plugin(monkeypatch):
    """Patch plugins manager to always and only return our custom timetable."""
    from airflow import plugins_manager

    monkeypatch.setattr(plugins_manager, "initialize_timetables_plugins", lambda: None)
    monkeypatch.setattr(
        plugins_manager,
        "timetable_classes",
        {"tests_common.test_utils.timetables.CustomSerializationTimetable": CustomSerializationTimetable},
    )


class TestStringifiedDAGs:
    """Unit tests for stringified DAGs."""

    def setup_method(self):
        logging.config.dictConfig(DEFAULT_LOGGING_CONFIG)

    @pytest.fixture(autouse=True)
    def setup_test_cases(self):
        with mock.patch.object(BaseHook, "get_connection") as m:
            m.return_value = Connection(
                extra=(
                    "{"
                    '"project_id": "mock", '
                    '"location": "mock", '
                    '"instance": "mock", '
                    '"database_type": "postgres", '
                    '"use_proxy": "False", '
                    '"use_ssl": "False"'
                    "}"
                )
            )

    # Skip that test if latest botocore is used - it reads all example dags and in case latest botocore
    # is upgraded to latest, usually aiobotocore can't be installed and some of the system tests will fail with
    # import errors.
    @pytest.mark.skipif(
        os.environ.get("UPGRADE_BOTO", "") == "true",
        reason="This test is skipped when latest botocore is installed",
    )
    @skip_if_force_lowest_dependencies_marker
    @pytest.mark.db_test
    def test_serialization(self):
        """Serialization and deserialization should work for every DAG and Operator."""
        with warnings.catch_warnings():
            dags, import_errors = collect_dags()
        serialized_dags = {}
        for v in dags.values():
            dag = SerializedDAG.to_dict(v)
            SerializedDAG.validate_schema(dag)
            serialized_dags[v.dag_id] = dag

        # Ignore some errors.
        import_errors = {
            file: error
            for file, error in import_errors.items()
            # Don't worry about warnings, we only care about errors here -- otherwise
            # AirflowProviderDeprecationWarning etc show up in import_errors, and being aware of all of those is
            # not relevant to this test; we only care about actual errors
            if "airflow.exceptions.AirflowProviderDeprecationWarning" not in error
            # TODO: TaskSDK
            if "`use_airflow_context=True` is not yet implemented" not in error
            # This "looks" like a problem, but is just a quirk of the parse-all-dags-in-one-process we do
            # in this test
            if "AirflowDagDuplicatedIdException: Ignoring DAG example_sagemaker" not in error
        }

        # Let's not be exact about this, but if everything fails to parse we should fail this test too
        assert import_errors == {}
        assert len(dags) > 100

        # Compares with the ground truth of JSON string.
        actual, expected = self.prepare_ser_dags_for_comparison(
            actual=serialized_dags["simple_dag"],
            expected=serialized_simple_dag_ground_truth,
        )
        assert actual == expected

    @pytest.mark.db_test
    @pytest.mark.parametrize(
        "timetable, serialized_timetable",
        [
            (
                cron_timetable("0 0 * * *"),
                {
                    "__type": "airflow.timetables.interval.CronDataIntervalTimetable",
                    "__var": {"expression": "0 0 * * *", "timezone": "UTC"},
                },
            ),
            (
                CustomSerializationTimetable("foo"),
                CUSTOM_TIMETABLE_SERIALIZED,
            ),
        ],
    )
    @pytest.mark.usefixtures("timetable_plugin")
    def test_dag_serialization_to_timetable(self, timetable, serialized_timetable):
        """Verify a timetable-backed DAG is serialized correctly."""
        dag = get_timetable_based_simple_dag(timetable)
        serialized_dag = SerializedDAG.to_dict(dag)
        SerializedDAG.validate_schema(serialized_dag)

        expected = copy.deepcopy(serialized_simple_dag_ground_truth)
        expected["dag"]["timetable"] = serialized_timetable

        # these tasks are not mapped / in mapped task group
        for task in expected["dag"]["tasks"]:
            task["__var"]["_needs_expansion"] = False

        actual, expected = self.prepare_ser_dags_for_comparison(
            actual=serialized_dag,
            expected=expected,
        )
        assert actual == expected

    @pytest.mark.db_test
    def test_dag_serialization_preserves_empty_access_roles(self):
        """Verify that an explicitly empty access_control dict is preserved."""
        dag = make_simple_dag()
        dag.access_control = {}
        serialized_dag = SerializedDAG.to_dict(dag)
        SerializedDAG.validate_schema(serialized_dag)

        assert serialized_dag["dag"]["access_control"] == {
            "__type": "dict",
            "__var": {},
        }

    @pytest.mark.db_test
    def test_dag_serialization_unregistered_custom_timetable(self):
        """Verify serialization fails without timetable registration."""
        dag = get_timetable_based_simple_dag(CustomSerializationTimetable("bar"))
        with pytest.raises(SerializationError) as ctx:
            SerializedDAG.to_dict(dag)

        message = (
            "Failed to serialize DAG 'simple_dag': Timetable class "
            "'tests_common.test_utils.timetables.CustomSerializationTimetable' "
            "is not registered or "
            "you have a top level database access that disrupted the session. "
            "Please check the airflow best practices documentation."
        )
        assert str(ctx.value) == message

    def prepare_ser_dags_for_comparison(self, actual, expected):
        """Verify serialized DAGs match the ground truth."""
        assert actual["dag"]["fileloc"].split("/")[-1] == "test_dag_serialization.py"
        actual["dag"]["fileloc"] = None

        def sorted_serialized_dag(dag_dict: dict):
            """
            Sorts the "tasks" list and "access_control" permissions in the
            serialised dag python dictionary. This is needed as the order of
            items should not matter but assertEqual would fail if the order of
            items changes in the dag dictionary
            """
            tasks = []
            for task in sorted(dag_dict["dag"]["tasks"], key=lambda x: x["__var"]["task_id"]):
                task["__var"] = dict(sorted(task["__var"].items(), key=lambda x: x[0]))
                tasks.append(task)
            dag_dict["dag"]["tasks"] = tasks
            if "access_control" in dag_dict["dag"]:
                dag_dict["dag"]["access_control"]["__var"]["test_role"]["__var"] = sorted(
                    dag_dict["dag"]["access_control"]["__var"]["test_role"]["__var"]
                )
            return dag_dict

        expected = copy.deepcopy(expected)

        # by roundtripping to json we get a cleaner diff
        # if not doing this, we get false alarms such as "__var" != VAR
        actual = json.loads(json.dumps(sorted_serialized_dag(actual)))
        expected = json.loads(json.dumps(sorted_serialized_dag(expected)))
        return actual, expected

    @pytest.mark.db_test
    def test_deserialization_across_process(self):
        """A serialized DAG can be deserialized in another process."""
        # Since we need to parse the dags twice here (once in the subprocess,
        # and once here to get a DAG to compare to) we don't want to load all
        # dags.
        queue = multiprocessing.Queue()
        proc = multiprocessing.Process(target=serialize_subprocess, args=(queue, "airflow/example_dags"))
        proc.daemon = True
        proc.start()

        stringified_dags = {}
        while True:
            v = queue.get()
            if v is None:
                break
            dag = SerializedDAG.from_json(v)
            assert isinstance(dag, DAG)
            stringified_dags[dag.dag_id] = dag

        dags, _ = collect_dags("airflow/example_dags")
        assert set(stringified_dags.keys()) == set(dags.keys())

        # Verify deserialized DAGs.
        for dag_id in stringified_dags:
            self.validate_deserialized_dag(stringified_dags[dag_id], dags[dag_id])

    @skip_if_force_lowest_dependencies_marker
    @pytest.mark.db_test
    def test_roundtrip_provider_example_dags(self):
        dags, _ = collect_dags(
            [
                "providers/*/src/airflow/providers/*/example_dags",
                "providers/*/src/airflow/providers/*/*/example_dags",
            ]
        )

        # Verify deserialized DAGs.
        for dag in dags.values():
            serialized_dag = SerializedDAG.from_json(SerializedDAG.to_json(dag))
            self.validate_deserialized_dag(serialized_dag, dag)

        # Let's not be exact about this, but if everything fails to parse we should fail this test too
        assert len(dags) >= 7

    @pytest.mark.db_test
    @pytest.mark.parametrize(
        "timetable",
        [cron_timetable("0 0 * * *"), CustomSerializationTimetable("foo")],
    )
    @pytest.mark.usefixtures("timetable_plugin")
    def test_dag_roundtrip_from_timetable(self, timetable):
        """Verify a timetable-backed serialization can be deserialized."""
        dag = get_timetable_based_simple_dag(timetable)
        roundtripped = SerializedDAG.from_json(SerializedDAG.to_json(dag))
        self.validate_deserialized_dag(roundtripped, dag)

    def validate_deserialized_dag(self, serialized_dag: DAG, dag: DAG):
        """
        Verify that all example DAGs work with DAG Serialization by
        checking fields between Serialized Dags & non-Serialized Dags
        """
        exclusion_list = {
            # Doesn't implement __eq__ properly. Check manually.
            "timetable",
            "timezone",
            # Need to check fields in it, to exclude functions.
            "default_args",
            "task_group",
            "params",
            "_processor_dags_folder",
        }
        fields_to_check = dag.get_serialized_fields() - exclusion_list
        for field in fields_to_check:
            actual = getattr(serialized_dag, field)
            expected = getattr(dag, field, None)

            assert actual == expected, f"{dag.dag_id}.{field} does not match"
        # _processor_dags_folder is only populated at serialization time
        # it's only used when relying on serialized dag to determine a dag's relative path
        assert (
            serialized_dag._processor_dags_folder
            == (AIRFLOW_REPO_ROOT_PATH / "airflow-core" / "tests" / "unit" / "dags").as_posix()
        )
        if dag.default_args:
            for k, v in dag.default_args.items():
                if callable(v):
                    # Check we stored _something_.
                    assert k in serialized_dag.default_args
                else:
                    assert v == serialized_dag.default_args[k], (
                        f"{dag.dag_id}.default_args[{k}] does not match"
                    )

        assert serialized_dag.timetable.summary == dag.timetable.summary
        assert serialized_dag.timetable.serialize() == dag.timetable.serialize()
        assert serialized_dag.timezone == dag.timezone

        for task_id in dag.task_ids:
            self.validate_deserialized_task(serialized_dag.get_task(task_id), dag.get_task(task_id))

    def validate_deserialized_task(
        self,
        serialized_task,
        task,
    ):
        """Verify non-Airflow operators are casted to BaseOperator or MappedOperator."""
        from airflow.sdk import BaseOperator
        from airflow.sdk.definitions.mappedoperator import MappedOperator

        assert not isinstance(task, SerializedBaseOperator)
        assert isinstance(task, (BaseOperator, MappedOperator))

        # Every task should have a task_group property -- even if it's the DAG's root task group
        assert serialized_task.task_group

        if isinstance(task, BaseOperator):
            assert isinstance(serialized_task, SerializedBaseOperator)
            fields_to_check = task.get_serialized_fields() - {
                # Checked separately
                "task_type",
                "_operator_name",
                # Type is excluded, so don't check it
                "_log",
                # List vs tuple. Check separately
                "template_ext",
                "template_fields",
                # We store the string, real dag has the actual code
                "_pre_execute_hook",
                "_post_execute_hook",
                "on_execute_callback",
                "on_failure_callback",
                "on_success_callback",
                "on_retry_callback",
                # Checked separately
                "resources",
                "on_failure_fail_dagrun",
                "_needs_expansion",
                "_is_sensor",
            }
        else:  # Promised to be mapped by the assert above.
            assert isinstance(serialized_task, MappedOperator)
            fields_to_check = {f.name for f in attrs.fields(MappedOperator)}
            fields_to_check -= {
                "map_index_template",
                # Matching logic in BaseOperator.get_serialized_fields().
                "dag",
                "task_group",
                # List vs tuple. Check separately.
                "operator_extra_links",
                "template_ext",
                "template_fields",
                # Checked separately.
                "operator_class",
                "partial_kwargs",
                "expand_input",
            }

        assert serialized_task.task_type == task.task_type

        assert set(serialized_task.template_ext) == set(task.template_ext)
        assert set(serialized_task.template_fields) == set(task.template_fields)

        assert serialized_task.upstream_task_ids == task.upstream_task_ids
        assert serialized_task.downstream_task_ids == task.downstream_task_ids

        for field in fields_to_check:
            assert getattr(serialized_task, field) == getattr(task, field), (
                f"{task.dag.dag_id}.{task.task_id}.{field} does not match"
            )

        if serialized_task.resources is None:
            assert task.resources is None or task.resources == []
        else:
            assert serialized_task.resources == task.resources

        # `deps` are set in the Scheduler's BaseOperator as that is where we need to evaluate deps
        # so only serialized tasks that are sensors should have the ReadyToRescheduleDep.
        if task._is_sensor:
            assert ReadyToRescheduleDep() in serialized_task.deps
        else:
            assert ReadyToRescheduleDep() not in serialized_task.deps

        # Ugly hack as some operators override params var in their init
        if isinstance(task.params, ParamsDict) and isinstance(serialized_task.params, ParamsDict):
            assert serialized_task.params.dump() == task.params.dump()

        if isinstance(task, MappedOperator):
            # MappedOperator.operator_class holds a backup of the serialized
            # data; checking its entirety basically duplicates this validation
            # function, so we just do some sanity checks.
            serialized_task.operator_class["task_type"] == type(task).__name__
            if isinstance(serialized_task.operator_class, DecoratedOperator):
                serialized_task.operator_class["_operator_name"] == task._operator_name

            # Serialization cleans up default values in partial_kwargs, this
            # adds them back to both sides.
            default_partial_kwargs = (
                BaseOperator.partial(task_id="_")._expand(EXPAND_INPUT_EMPTY, strict=False).partial_kwargs
            )
            serialized_partial_kwargs = {
                **default_partial_kwargs,
                **serialized_task.partial_kwargs,
            }
            original_partial_kwargs = {**default_partial_kwargs, **task.partial_kwargs}
            assert serialized_partial_kwargs == original_partial_kwargs

            # ExpandInputs have different classes between scheduler and definition
            assert attrs.asdict(serialized_task.expand_input) == attrs.asdict(task.expand_input)

    @pytest.mark.parametrize(
        "dag_start_date, task_start_date, expected_task_start_date",
        [
            (
                datetime(2019, 8, 1, tzinfo=timezone.utc),
                None,
                datetime(2019, 8, 1, tzinfo=timezone.utc),
            ),
            (
                datetime(2019, 8, 1, tzinfo=timezone.utc),
                datetime(2019, 8, 2, tzinfo=timezone.utc),
                datetime(2019, 8, 2, tzinfo=timezone.utc),
            ),
            (
                datetime(2019, 8, 1, tzinfo=timezone.utc),
                datetime(2019, 7, 30, tzinfo=timezone.utc),
                datetime(2019, 8, 1, tzinfo=timezone.utc),
            ),
            (
                datetime(2019, 8, 1, tzinfo=dt_timezone(timedelta(hours=1))),
                datetime(2019, 7, 30, tzinfo=dt_timezone(timedelta(hours=1))),
                datetime(2019, 8, 1, tzinfo=dt_timezone(timedelta(hours=1))),
            ),
            (
                pendulum.datetime(2019, 8, 1, tz="UTC"),
                None,
                pendulum.datetime(2019, 8, 1, tz="UTC"),
            ),
        ],
    )
    def test_deserialization_start_date(self, dag_start_date, task_start_date, expected_task_start_date):
        dag = DAG(dag_id="simple_dag", schedule=None, start_date=dag_start_date)
        BaseOperator(task_id="simple_task", dag=dag, start_date=task_start_date)

        serialized_dag = SerializedDAG.to_dict(dag)
        if not task_start_date or dag_start_date >= task_start_date:
            # If dag.start_date > task.start_date -> task.start_date=dag.start_date
            # because of the logic in dag.add_task()
            assert "start_date" not in serialized_dag["dag"]["tasks"][0]["__var"]
        else:
            assert "start_date" in serialized_dag["dag"]["tasks"][0]["__var"]

        dag = SerializedDAG.from_dict(serialized_dag)
        simple_task = dag.task_dict["simple_task"]
        assert simple_task.start_date == expected_task_start_date

    def test_deserialization_with_dag_context(self):
        with DAG(
            dag_id="simple_dag",
            schedule=None,
            start_date=datetime(2019, 8, 1, tzinfo=timezone.utc),
        ) as dag:
            BaseOperator(task_id="simple_task")
            # should not raise RuntimeError: dictionary changed size during iteration
            SerializedDAG.to_dict(dag)

    @pytest.mark.parametrize(
        "dag_end_date, task_end_date, expected_task_end_date",
        [
            (
                datetime(2019, 8, 1, tzinfo=timezone.utc),
                None,
                datetime(2019, 8, 1, tzinfo=timezone.utc),
            ),
            (
                datetime(2019, 8, 1, tzinfo=timezone.utc),
                datetime(2019, 8, 2, tzinfo=timezone.utc),
                datetime(2019, 8, 1, tzinfo=timezone.utc),
            ),
            (
                datetime(2019, 8, 1, tzinfo=timezone.utc),
                datetime(2019, 7, 30, tzinfo=timezone.utc),
                datetime(2019, 7, 30, tzinfo=timezone.utc),
            ),
        ],
    )
    def test_deserialization_end_date(self, dag_end_date, task_end_date, expected_task_end_date):
        dag = DAG(
            dag_id="simple_dag",
            schedule=None,
            start_date=datetime(2019, 8, 1),
            end_date=dag_end_date,
        )
        BaseOperator(task_id="simple_task", dag=dag, end_date=task_end_date)

        serialized_dag = SerializedDAG.to_dict(dag)
        if not task_end_date or dag_end_date <= task_end_date:
            # If dag.end_date < task.end_date -> task.end_date=dag.end_date
            # because of the logic in dag.add_task()
            assert "end_date" not in serialized_dag["dag"]["tasks"][0]["__var"]
        else:
            assert "end_date" in serialized_dag["dag"]["tasks"][0]["__var"]

        dag = SerializedDAG.from_dict(serialized_dag)
        simple_task = dag.task_dict["simple_task"]
        assert simple_task.end_date == expected_task_end_date

    @pytest.mark.parametrize(
        "serialized_timetable, expected_timetable",
        [
            (
                {"__type": "airflow.timetables.simple.NullTimetable", "__var": {}},
                NullTimetable(),
            ),
            (
                {
                    "__type": "airflow.timetables.interval.CronDataIntervalTimetable",
                    "__var": {"expression": "@weekly", "timezone": "UTC"},
                },
                cron_timetable("0 0 * * 0"),
            ),
            (
                {"__type": "airflow.timetables.simple.OnceTimetable", "__var": {}},
                OnceTimetable(),
            ),
            (
                {
                    "__type": "airflow.timetables.interval.DeltaDataIntervalTimetable",
                    "__var": {"delta": 86400.0},
                },
                delta_timetable(timedelta(days=1)),
            ),
            (CUSTOM_TIMETABLE_SERIALIZED, CustomSerializationTimetable("foo")),
        ],
    )
    @pytest.mark.usefixtures("timetable_plugin")
    def test_deserialization_timetable(
        self,
        serialized_timetable,
        expected_timetable,
    ):
        serialized = {
            "__version": 2,
            "dag": {
                "default_args": {"__type": "dict", "__var": {}},
                "dag_id": "simple_dag",
                "fileloc": __file__,
                "tasks": [],
                "timezone": "UTC",
                "timetable": serialized_timetable,
            },
        }
        SerializedDAG.validate_schema(serialized)
        dag = SerializedDAG.from_dict(serialized)
        assert dag.timetable == expected_timetable

    def test_deserialization_timetable_unregistered(self):
        serialized = {
            "__version": 2,
            "dag": {
                "default_args": {"__type": "dict", "__var": {}},
                "dag_id": "simple_dag",
                "fileloc": __file__,
                "tasks": [],
                "timezone": "UTC",
                "timetable": CUSTOM_TIMETABLE_SERIALIZED,
            },
        }
        SerializedDAG.validate_schema(serialized)
        with pytest.raises(ValueError) as ctx:
            SerializedDAG.from_dict(serialized)
        message = (
            "Timetable class "
            "'tests_common.test_utils.timetables.CustomSerializationTimetable' "
            "is not registered or "
            "you have a top level database access that disrupted the session. "
            "Please check the airflow best practices documentation."
        )
        assert str(ctx.value) == message

    @pytest.mark.parametrize(
        "val, expected",
        [
            (
                relativedelta(days=-1),
                {"__type": "relativedelta", "__var": {"days": -1}},
            ),
            (
                relativedelta(month=1, days=-1),
                {"__type": "relativedelta", "__var": {"month": 1, "days": -1}},
            ),
            # Every friday
            (
                relativedelta(weekday=FR),
                {"__type": "relativedelta", "__var": {"weekday": [4]}},
            ),
            # Every second friday
            (
                relativedelta(weekday=FR(2)),
                {"__type": "relativedelta", "__var": {"weekday": [4, 2]}},
            ),
        ],
    )
    def test_roundtrip_relativedelta(self, val, expected):
        serialized = SerializedDAG.serialize(val)
        assert serialized == expected

        round_tripped = SerializedDAG.deserialize(serialized)
        assert val == round_tripped

    @pytest.mark.parametrize(
        "val, expected_val",
        [
            (None, {}),
            ({"param_1": "value_1"}, {"param_1": "value_1"}),
            ({"param_1": {1, 2, 3}}, ParamValidationError),
        ],
    )
    def test_dag_params_roundtrip(self, val, expected_val):
        """
        Test that params work both on Serialized DAGs & Tasks
        """
        if expected_val == ParamValidationError:
            with pytest.raises(ParamValidationError):
                dag = DAG(dag_id="simple_dag", schedule=None, params=val)
            # further tests not relevant
            return
        dag = DAG(dag_id="simple_dag", schedule=None, params=val)
        BaseOperator(task_id="simple_task", dag=dag, start_date=datetime(2019, 8, 1))

        serialized_dag_json = SerializedDAG.to_json(dag)

        serialized_dag = json.loads(serialized_dag_json)

        assert "params" in serialized_dag["dag"]

        deserialized_dag = SerializedDAG.from_dict(serialized_dag)
        deserialized_simple_task = deserialized_dag.task_dict["simple_task"]
        assert expected_val == deserialized_dag.params.dump()
        assert expected_val == deserialized_simple_task.params.dump()

    def test_invalid_params(self):
        """
        Test to make sure that only native Param objects are being passed as dag or task params
        """

        class S3Param(Param):
            def __init__(self, path: str):
                schema = {"type": "string", "pattern": r"s3:\/\/(.+?)\/(.+)"}
                super().__init__(default=path, schema=schema)

        dag = DAG(
            dag_id="simple_dag",
            schedule=None,
            params={"path": S3Param("s3://my_bucket/my_path")},
        )

        with pytest.raises(SerializationError):
            SerializedDAG.to_dict(dag)

        dag = DAG(dag_id="simple_dag", schedule=None)
        BaseOperator(
            task_id="simple_task",
            dag=dag,
            start_date=datetime(2019, 8, 1),
            params={"path": S3Param("s3://my_bucket/my_path")},
        )

    @pytest.mark.parametrize(
        "param",
        [
            Param("my value", description="hello", schema={"type": "string"}),
            Param("my value", description="hello"),
            Param(None, description=None),
            Param([True], type="array", items={"type": "boolean"}),
            Param(),
        ],
    )
    def test_full_param_roundtrip(self, param: Param):
        """
        Test to make sure that only native Param objects are being passed as dag or task params
        """
        dag = DAG(dag_id="simple_dag", schedule=None, params={"my_param": param})
        serialized_json = SerializedDAG.to_json(dag)
        serialized = json.loads(serialized_json)
        SerializedDAG.validate_schema(serialized)
        dag = SerializedDAG.from_dict(serialized)

        assert dag.params.get_param("my_param").value == param.value
        observed_param = dag.params.get_param("my_param")
        assert isinstance(observed_param, Param)
        assert observed_param.description == param.description
        assert observed_param.schema == param.schema

    @pytest.mark.parametrize(
        "val, expected_val",
        [
            (None, {}),
            ({"param_1": "value_1"}, {"param_1": "value_1"}),
            ({"param_1": {1, 2, 3}}, ParamValidationError),
        ],
    )
    def test_task_params_roundtrip(self, val, expected_val):
        """
        Test that params work both on Serialized DAGs & Tasks
        """
        dag = DAG(dag_id="simple_dag", schedule=None)
        if expected_val == ParamValidationError:
            with pytest.raises(ParamValidationError):
                BaseOperator(
                    task_id="simple_task",
                    dag=dag,
                    params=val,
                    start_date=datetime(2019, 8, 1),
                )
            # further tests not relevant
            return
        BaseOperator(
            task_id="simple_task",
            dag=dag,
            params=val,
            start_date=datetime(2019, 8, 1),
        )
        serialized_dag = SerializedDAG.to_dict(dag)
        deserialized_dag = SerializedDAG.from_dict(serialized_dag)

        if val:
            assert "params" in serialized_dag["dag"]["tasks"][0]["__var"]
        else:
            assert "params" not in serialized_dag["dag"]["tasks"][0]["__var"]

        deserialized_simple_task = deserialized_dag.task_dict["simple_task"]
        assert expected_val == deserialized_simple_task.params.dump()

    @pytest.mark.db_test
    @pytest.mark.parametrize(
        ("bash_command", "serialized_links", "links"),
        [
            pytest.param(
                "true",
                {"Google Custom": "_link_CustomOpLink"},
                {"Google Custom": "http://google.com/custom_base_link?search=true"},
                id="non-indexed-link",
            ),
            pytest.param(
                ["echo", "true"],
                {"BigQuery Console #1": "bigquery_1", "BigQuery Console #2": "bigquery_2"},
                {
                    "BigQuery Console #1": "https://console.cloud.google.com/bigquery?j=echo",
                    "BigQuery Console #2": "https://console.cloud.google.com/bigquery?j=true",
                },
                id="multiple-indexed-links",
            ),
        ],
    )
    def test_extra_serialized_field_and_operator_links(
        self, bash_command, serialized_links, links, dag_maker
    ):
        """
        Assert extra field exists & OperatorLinks defined in Plugins and inbuilt Operator Links.

        This tests also depends on GoogleLink() registered as a plugin
        in tests/plugins/test_plugin.py

        The function tests that if extra operator links are registered in plugin
        in ``operator_extra_links`` and the same is also defined in
        the Operator in ``BaseOperator.operator_extra_links``, it has the correct
        extra link.

        If CustomOperator is called with a string argument for bash_command it
        has a single link, if called with an array it has one link per element.
        We use this to test the serialization of link data.
        """
        test_date = timezone.DateTime(2019, 8, 1, tzinfo=timezone.utc)

        with dag_maker(dag_id="simple_dag", start_date=test_date) as dag:
            CustomOperator(task_id="simple_task", bash_command=bash_command)

        serialized_dag = SerializedDAG.to_dict(dag)
        assert "bash_command" in serialized_dag["dag"]["tasks"][0]["__var"]

        dag = SerializedDAG.from_dict(serialized_dag)
        simple_task = dag.task_dict["simple_task"]
        assert getattr(simple_task, "bash_command") == bash_command

        #########################################################
        # Verify Operator Links work with Serialized Operator
        #########################################################
        # Check Serialized version of operator link only contains the inbuilt Op Link
        assert serialized_dag["dag"]["tasks"][0]["__var"]["_operator_extra_links"] == serialized_links

        # Test all the extra_links are set
        assert simple_task.extra_links == sorted({*links, "airflow", "github", "google"})

        dr = dag_maker.create_dagrun(logical_date=test_date)
        (ti,) = dr.task_instances
        XComModel.set(
            key="search_query",
            value=bash_command,
            task_id=simple_task.task_id,
            dag_id=simple_task.dag_id,
            run_id=dr.run_id,
        )

        # Test Deserialized inbuilt link
        for i, (name, expected) in enumerate(links.items()):
            # staging the part where a task at runtime pushes xcom for extra links
            XComModel.set(
                key=simple_task.operator_extra_links[i].xcom_key,
                value=expected,
                task_id=simple_task.task_id,
                dag_id=simple_task.dag_id,
                run_id=dr.run_id,
            )

            link = simple_task.get_extra_links(ti, name)
            assert link == expected
        current_python_version = sys.version_info[:2]
        if current_python_version >= (3, 13):
            # TODO(potiuk) We should bring it back when ray is supported on Python 3.13
            # Test Deserialized link registered via Airflow Plugin
            from tests_common.test_utils.mock_operators import GoogleLink

            link = simple_task.get_extra_links(ti, GoogleLink.name)
            assert link == "https://www.google.com"

    class ClassWithCustomAttributes:
        """
        Class for testing purpose: allows to create objects with custom attributes in one single statement.
        """

        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

        def __str__(self):
            return f"{self.__class__.__name__}({str(self.__dict__)})"

        def __repr__(self):
            return self.__str__()

        def __eq__(self, other):
            return self.__dict__ == other.__dict__

        def __ne__(self, other):
            return not self.__eq__(other)

    @pytest.mark.parametrize(
        "templated_field, expected_field",
        [
            (None, None),
            ([], []),
            ({}, {}),
            ("{{ task.task_id }}", "{{ task.task_id }}"),
            (["{{ task.task_id }}", "{{ task.task_id }}"]),
            ({"foo": "{{ task.task_id }}"}, {"foo": "{{ task.task_id }}"}),
            (
                {"foo": {"bar": "{{ task.task_id }}"}},
                {"foo": {"bar": "{{ task.task_id }}"}},
            ),
            (
                [
                    {"foo1": {"bar": "{{ task.task_id }}"}},
                    {"foo2": {"bar": "{{ task.task_id }}"}},
                ],
                [
                    {"foo1": {"bar": "{{ task.task_id }}"}},
                    {"foo2": {"bar": "{{ task.task_id }}"}},
                ],
            ),
            (
                {"foo": {"bar": {"{{ task.task_id }}": ["sar"]}}},
                {"foo": {"bar": {"{{ task.task_id }}": ["sar"]}}},
            ),
            (
                ClassWithCustomAttributes(
                    att1="{{ task.task_id }}",
                    att2="{{ task.task_id }}",
                    template_fields=["att1"],
                ),
                "ClassWithCustomAttributes("
                "{'att1': '{{ task.task_id }}', 'att2': '{{ task.task_id }}', 'template_fields': ['att1']})",
            ),
            (
                ClassWithCustomAttributes(
                    nested1=ClassWithCustomAttributes(
                        att1="{{ task.task_id }}",
                        att2="{{ task.task_id }}",
                        template_fields=["att1"],
                    ),
                    nested2=ClassWithCustomAttributes(
                        att3="{{ task.task_id }}",
                        att4="{{ task.task_id }}",
                        template_fields=["att3"],
                    ),
                    template_fields=["nested1"],
                ),
                "ClassWithCustomAttributes("
                "{'nested1': ClassWithCustomAttributes({'att1': '{{ task.task_id }}', "
                "'att2': '{{ task.task_id }}', 'template_fields': ['att1']}), "
                "'nested2': ClassWithCustomAttributes({'att3': '{{ task.task_id }}', 'att4': "
                "'{{ task.task_id }}', 'template_fields': ['att3']}), 'template_fields': ['nested1']})",
            ),
        ],
    )
    def test_templated_fields_exist_in_serialized_dag(self, templated_field, expected_field):
        """
        Test that templated_fields exists for all Operators in Serialized DAG

        Since we don't want to inflate arbitrary python objects (it poses a RCE/security risk etc.)
        we want check that non-"basic" objects are turned in to strings after deserializing.
        """
        dag = DAG(
            "test_serialized_template_fields",
            schedule=None,
            start_date=datetime(2019, 8, 1),
        )
        with dag:
            BashOperator(task_id="test", bash_command=templated_field)

        serialized_dag = SerializedDAG.to_dict(dag)
        deserialized_dag = SerializedDAG.from_dict(serialized_dag)
        deserialized_test_task = deserialized_dag.task_dict["test"]
        assert expected_field == getattr(deserialized_test_task, "bash_command")

    def test_dag_serialized_fields_with_schema(self):
        """
        Additional Properties are disabled on DAGs. This test verifies that all the
        keys in DAG.get_serialized_fields are listed in Schema definition.
        """
        dag_schema: dict = load_dag_schema_dict()["definitions"]["dag"]["properties"]

        # The parameters we add manually in Serialization need to be ignored
        ignored_keys: set = {
            "tasks",
            "has_on_success_callback",
            "has_on_failure_callback",
            "dag_dependencies",
            "params",
        }

        keys_for_backwards_compat: set = {
            "_concurrency",
        }
        dag_params: set = set(dag_schema.keys()) - ignored_keys - keys_for_backwards_compat
        assert set(DAG.get_serialized_fields()) == dag_params

    def test_operator_subclass_changing_base_defaults(self):
        assert BaseOperator(task_id="dummy").do_xcom_push is True, (
            "Precondition check! If this fails the test won't make sense"
        )

        class MyOperator(BaseOperator):
            def __init__(self, do_xcom_push=False, **kwargs):
                super().__init__(**kwargs)
                self.do_xcom_push = do_xcom_push

        op = MyOperator(task_id="dummy")
        assert op.do_xcom_push is False

        blob = SerializedBaseOperator.serialize_operator(op)
        serialized_op = SerializedBaseOperator.deserialize_operator(blob)

        assert serialized_op.do_xcom_push is False

    def test_no_new_fields_added_to_base_operator(self):
        """
        This test verifies that there are no new fields added to BaseOperator. And reminds that
        tests should be added for it.
        """
        from airflow.utils.trigger_rule import TriggerRule

        base_operator = BaseOperator(task_id="10")
        # Return the name of any annotated class property, or anything explicitly listed in serialized fields
        field_names = {
            fld.name
            for fld in dataclasses.fields(BaseOperator)
            if fld.name in BaseOperator.get_serialized_fields()
        } | BaseOperator.get_serialized_fields()
        fields = {k: getattr(base_operator, k) for k in field_names}
        assert fields == {
            "_logger_name": None,
            "_needs_expansion": None,
            "_post_execute_hook": None,
            "_pre_execute_hook": None,
            "_task_display_name": None,
            "allow_nested_operators": True,
            "depends_on_past": False,
            "do_xcom_push": True,
            "doc": None,
            "doc_json": None,
            "doc_md": None,
            "doc_rst": None,
            "doc_yaml": None,
            "downstream_task_ids": set(),
            "end_date": None,
            "email": None,
            "email_on_failure": True,
            "email_on_retry": True,
            "execution_timeout": None,
            "executor": None,
            "executor_config": {},
            "ignore_first_depends_on_past": False,
            "is_setup": False,
            "is_teardown": False,
            "inlets": [],
            "map_index_template": None,
            "max_active_tis_per_dag": None,
            "max_active_tis_per_dagrun": None,
            "max_retry_delay": None,
            "on_execute_callback": [],
            "on_failure_fail_dagrun": False,
            "on_failure_callback": [],
            "on_retry_callback": [],
            "on_skipped_callback": [],
            "on_success_callback": [],
            "outlets": [],
            "owner": "airflow",
            "params": {},
            "pool": "default_pool",
            "pool_slots": 1,
            "priority_weight": 1,
            "queue": "default",
            "resources": None,
            "retries": 0,
            "retry_delay": timedelta(0, 300),
            "retry_exponential_backoff": False,
            "run_as_user": None,
            "start_date": None,
            "start_from_trigger": False,
            "start_trigger_args": None,
            "task_id": "10",
            "task_type": "BaseOperator",
            "template_ext": (),
            "template_fields": (),
            "template_fields_renderers": {},
            "trigger_rule": TriggerRule.ALL_SUCCESS,
            "ui_color": "#fff",
            "ui_fgcolor": "#000",
            "wait_for_downstream": False,
            "wait_for_past_depends_before_skipping": False,
            "weight_rule": _DownstreamPriorityWeightStrategy(),
            "multiple_outputs": False,
        }, """
!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

     ACTION NEEDED! PLEASE READ THIS CAREFULLY AND CORRECT TESTS CAREFULLY

 Some fields were added to the BaseOperator! Please add them to the list above and make sure that
 you add support for DAG serialization - you should add the field to
 `airflow/serialization/schema.json` - they should have correct type defined there.

 Note that we do not support versioning yet so you should only add optional fields to BaseOperator.

!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
                         """

    def test_operator_deserialize_old_names(self):
        blob = {
            "task_id": "custom_task",
            "_downstream_task_ids": ["foo"],
            "template_ext": [],
            "template_fields": ["bash_command"],
            "template_fields_renderers": {},
            "task_type": "CustomOperator",
            "_task_module": "tests_common.test_utils.mock_operators",
            "pool": "default_pool",
            "ui_color": "#fff",
            "ui_fgcolor": "#000",
        }

        SerializedDAG._json_schema.validate(blob, _schema=load_dag_schema_dict()["definitions"]["operator"])
        serialized_op = SerializedBaseOperator.deserialize_operator(blob)
        assert serialized_op.downstream_task_ids == {"foo"}

    def test_task_resources(self):
        """
        Test task resources serialization/deserialization.
        """
        from airflow.providers.standard.operators.empty import EmptyOperator

        logical_date = datetime(2020, 1, 1)
        task_id = "task1"
        with DAG("test_task_resources", schedule=None, start_date=logical_date) as dag:
            task = EmptyOperator(task_id=task_id, resources={"cpus": 0.1, "ram": 2048})

        SerializedDAG.validate_schema(SerializedDAG.to_dict(dag))

        json_dag = SerializedDAG.from_json(SerializedDAG.to_json(dag))
        deserialized_task = json_dag.get_task(task_id)
        assert deserialized_task.resources == task.resources
        assert isinstance(deserialized_task.resources, Resources)

    def test_task_group_serialization(self):
        """
        Test TaskGroup serialization/deserialization.
        """
        from airflow.providers.standard.operators.empty import EmptyOperator

        logical_date = datetime(2020, 1, 1)
        with DAG("test_task_group_serialization", schedule=None, start_date=logical_date) as dag:
            task1 = EmptyOperator(task_id="task1")
            with TaskGroup("group234") as group234:
                _ = EmptyOperator(task_id="task2")

                with TaskGroup("group34") as group34:
                    _ = EmptyOperator(task_id="task3")
                    _ = EmptyOperator(task_id="task4")

            task5 = EmptyOperator(task_id="task5")
            task1 >> group234
            group34 >> task5

        dag_dict = SerializedDAG.to_dict(dag)
        SerializedDAG.validate_schema(dag_dict)
        json_dag = SerializedDAG.from_json(SerializedDAG.to_json(dag))
        self.validate_deserialized_dag(json_dag, dag)

        serialized_dag = SerializedDAG.deserialize_dag(SerializedDAG.serialize_dag(dag))

        assert serialized_dag.task_group.children
        assert serialized_dag.task_group.children.keys() == dag.task_group.children.keys()

        def check_task_group(node):
            assert node.dag is serialized_dag
            try:
                children = node.children.values()
            except AttributeError:
                # Round-trip serialization and check the result
                expected_serialized = SerializedBaseOperator.serialize_operator(dag.get_task(node.task_id))
                expected_deserialized = SerializedBaseOperator.deserialize_operator(expected_serialized)
                expected_dict = SerializedBaseOperator.serialize_operator(expected_deserialized)
                assert node
                assert SerializedBaseOperator.serialize_operator(node) == expected_dict
                return

            for child in children:
                check_task_group(child)

        check_task_group(serialized_dag.task_group)

    @staticmethod
    def assert_taskgroup_children(se_task_group, dag_task_group, expected_children):
        assert se_task_group.children.keys() == dag_task_group.children.keys() == expected_children

    @staticmethod
    def assert_task_is_setup_teardown(task, is_setup: bool = False, is_teardown: bool = False):
        assert task.is_setup == is_setup
        assert task.is_teardown == is_teardown

    def test_setup_teardown_tasks(self):
        """
        Test setup and teardown task serialization/deserialization.
        """
        from airflow.providers.standard.operators.empty import EmptyOperator

        logical_date = datetime(2020, 1, 1)
        with DAG(
            "test_task_group_setup_teardown_tasks",
            schedule=None,
            start_date=logical_date,
        ) as dag:
            EmptyOperator(task_id="setup").as_setup()
            EmptyOperator(task_id="teardown").as_teardown()

            with TaskGroup("group1"):
                EmptyOperator(task_id="setup1").as_setup()
                EmptyOperator(task_id="task1")
                EmptyOperator(task_id="teardown1").as_teardown()

                with TaskGroup("group2"):
                    EmptyOperator(task_id="setup2").as_setup()
                    EmptyOperator(task_id="task2")
                    EmptyOperator(task_id="teardown2").as_teardown()

        dag_dict = SerializedDAG.to_dict(dag)
        SerializedDAG.validate_schema(dag_dict)
        json_dag = SerializedDAG.from_json(SerializedDAG.to_json(dag))
        self.validate_deserialized_dag(json_dag, dag)

        serialized_dag = SerializedDAG.deserialize_dag(SerializedDAG.serialize_dag(dag))

        self.assert_taskgroup_children(
            serialized_dag.task_group, dag.task_group, {"setup", "teardown", "group1"}
        )
        self.assert_task_is_setup_teardown(serialized_dag.task_group.children["setup"], is_setup=True)
        self.assert_task_is_setup_teardown(serialized_dag.task_group.children["teardown"], is_teardown=True)

        se_first_group = serialized_dag.task_group.children["group1"]
        dag_first_group = dag.task_group.children["group1"]
        self.assert_taskgroup_children(
            se_first_group,
            dag_first_group,
            {"group1.setup1", "group1.task1", "group1.group2", "group1.teardown1"},
        )
        self.assert_task_is_setup_teardown(se_first_group.children["group1.setup1"], is_setup=True)
        self.assert_task_is_setup_teardown(se_first_group.children["group1.task1"])
        self.assert_task_is_setup_teardown(se_first_group.children["group1.teardown1"], is_teardown=True)

        se_second_group = se_first_group.children["group1.group2"]
        dag_second_group = dag_first_group.children["group1.group2"]
        self.assert_taskgroup_children(
            se_second_group,
            dag_second_group,
            {"group1.group2.setup2", "group1.group2.task2", "group1.group2.teardown2"},
        )
        self.assert_task_is_setup_teardown(se_second_group.children["group1.group2.setup2"], is_setup=True)
        self.assert_task_is_setup_teardown(se_second_group.children["group1.group2.task2"])
        self.assert_task_is_setup_teardown(
            se_second_group.children["group1.group2.teardown2"], is_teardown=True
        )

    @pytest.mark.db_test
    def test_teardown_task_on_failure_fail_dagrun_serialization(self, dag_maker):
        with dag_maker() as dag:

            @teardown(on_failure_fail_dagrun=True)
            def mytask():
                print(1)

            mytask()

        dag_dict = SerializedDAG.to_dict(dag)
        SerializedDAG.validate_schema(dag_dict)
        json_dag = SerializedDAG.from_json(SerializedDAG.to_json(dag))
        self.validate_deserialized_dag(json_dag, dag)

        serialized_dag = SerializedDAG.deserialize_dag(SerializedDAG.serialize_dag(dag))
        task = serialized_dag.task_group.children["mytask"]
        assert task.is_teardown is True
        assert task.on_failure_fail_dagrun is True

    @pytest.mark.db_test
    def test_basic_mapped_dag(self, dag_maker):
        dagbag = DagBag(
            "airflow-core/src/airflow/example_dags/example_dynamic_task_mapping.py", include_examples=False
        )
        assert not dagbag.import_errors
        dag = dagbag.dags["example_dynamic_task_mapping"]
        ser_dag = SerializedDAG.to_dict(dag)
        # We should not include `_is_sensor` most of the time (as it would be wasteful). Check we don't
        assert "_is_sensor" not in ser_dag["dag"]["tasks"][0]["__var"]
        SerializedDAG.validate_schema(ser_dag)

    @pytest.mark.db_test
    def test_teardown_mapped_serialization(self, dag_maker):
        with dag_maker() as dag:

            @teardown(on_failure_fail_dagrun=True)
            def mytask(val=None):
                print(1)

            mytask.expand(val=[1, 2, 3])

        task = dag.task_group.children["mytask"]
        assert task.partial_kwargs["is_teardown"] is True
        assert task.partial_kwargs["on_failure_fail_dagrun"] is True

        dag_dict = SerializedDAG.to_dict(dag)
        SerializedDAG.validate_schema(dag_dict)
        json_dag = SerializedDAG.from_json(SerializedDAG.to_json(dag))
        self.validate_deserialized_dag(json_dag, dag)

        serialized_dag = SerializedDAG.deserialize_dag(SerializedDAG.serialize_dag(dag))
        task = serialized_dag.task_group.children["mytask"]
        assert task.partial_kwargs["is_teardown"] is True
        assert task.partial_kwargs["on_failure_fail_dagrun"] is True

    def test_serialize_mapped_outlets(self):
        with DAG(dag_id="d", schedule=None, start_date=datetime.now()):
            op = MockOperator.partial(task_id="x").expand(arg1=[1, 2])

        assert op.inlets == []
        assert op.outlets == []

        serialized = SerializedBaseOperator.serialize_mapped_operator(op)
        assert "inlets" not in serialized
        assert "outlets" not in serialized

        round_tripped = SerializedBaseOperator.deserialize_operator(serialized)
        assert isinstance(round_tripped, MappedOperator)
        assert round_tripped.inlets == []
        assert round_tripped.outlets == []

    @pytest.mark.db_test
    @pytest.mark.parametrize("mapped", [False, True])
    def test_derived_dag_deps_sensor(self, mapped):
        """
        Tests DAG dependency detection for sensors, including derived classes
        """
        from airflow.providers.standard.operators.empty import EmptyOperator
        from airflow.providers.standard.sensors.external_task import ExternalTaskSensor

        class DerivedSensor(ExternalTaskSensor):
            pass

        logical_date = datetime(2020, 1, 1)
        for class_ in [ExternalTaskSensor, DerivedSensor]:
            with DAG(dag_id="test_derived_dag_deps_sensor", schedule=None, start_date=logical_date) as dag:
                if mapped:
                    task1 = class_.partial(
                        task_id="task1",
                        external_dag_id="external_dag_id",
                        mode="reschedule",
                    ).expand(external_task_id=["some_task", "some_other_task"])
                else:
                    task1 = class_(
                        task_id="task1",
                        external_dag_id="external_dag_id",
                        mode="reschedule",
                    )

                task2 = EmptyOperator(task_id="task2")
                task1 >> task2

            dag = SerializedDAG.to_dict(dag)
            assert dag["dag"]["dag_dependencies"] == [
                {
                    "source": "external_dag_id",
                    "target": "test_derived_dag_deps_sensor",
                    "label": "task1",
                    "dependency_type": "sensor",
                    "dependency_id": "task1",
                }
            ]

    @pytest.mark.db_test
    def test_dag_deps_assets_with_duplicate_asset(self, testing_assets):
        """
        Check that dag_dependencies node is populated correctly for a DAG with duplicate assets.
        """
        from airflow.providers.standard.sensors.external_task import ExternalTaskSensor

        logical_date = datetime(2020, 1, 1)
        with DAG(dag_id="test", start_date=logical_date, schedule=[testing_assets[0]] * 5) as dag:
            ExternalTaskSensor(
                task_id="task1",
                external_dag_id="external_dag_id",
                mode="reschedule",
            )
            BashOperator(
                task_id="asset_writer",
                bash_command="echo hello",
                outlets=[testing_assets[1]] * 3 + testing_assets[2:3],
            )

            @dag.task(outlets=[testing_assets[3]])
            def other_asset_writer(x):
                pass

            other_asset_writer.expand(x=[1, 2])

        testing_asset_key_strs = [AssetUniqueKey.from_asset(asset).to_str() for asset in testing_assets]

        dag = SerializedDAG.to_dict(dag)
        actual = sorted(dag["dag"]["dag_dependencies"], key=lambda x: tuple(x.values()))
        expected = sorted(
            [
                {
                    "source": "test",
                    "target": "asset",
                    "label": "asset4",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[3],
                },
                {
                    "source": "external_dag_id",
                    "target": "test",
                    "label": "task1",
                    "dependency_type": "sensor",
                    "dependency_id": "task1",
                },
                {
                    "source": "test",
                    "target": "asset",
                    "label": "asset3",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[2],
                },
                {
                    "source": "test",
                    "target": "asset",
                    "label": "asset2",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[1],
                },
                {
                    "source": "asset",
                    "target": "test",
                    "label": "asset1",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[0],
                },
                {
                    "source": "asset",
                    "target": "test",
                    "label": "asset1",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[0],
                },
                {
                    "source": "asset",
                    "target": "test",
                    "label": "asset1",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[0],
                },
                {
                    "source": "asset",
                    "target": "test",
                    "label": "asset1",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[0],
                },
                {
                    "source": "asset",
                    "target": "test",
                    "label": "asset1",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[0],
                },
            ],
            key=lambda x: tuple(x.values()),
        )
        assert actual == expected

    @pytest.mark.db_test
    def test_dag_deps_assets(self, testing_assets):
        """
        Check that dag_dependencies node is populated correctly for a DAG with assets.

        Note that asset id will not be stored at this stage and will be later evaluated when
        calling SerializedDagModel.get_dag_dependencies.
        """
        from airflow.providers.standard.sensors.external_task import ExternalTaskSensor

        logical_date = datetime(2020, 1, 1)
        with DAG(dag_id="test", start_date=logical_date, schedule=testing_assets[0:1]) as dag:
            ExternalTaskSensor(
                task_id="task1",
                external_dag_id="external_dag_id",
                mode="reschedule",
            )
            BashOperator(task_id="asset_writer", bash_command="echo hello", outlets=testing_assets[1:3])

            @dag.task(outlets=testing_assets[3:])
            def other_asset_writer(x):
                pass

            other_asset_writer.expand(x=[1, 2])

        testing_asset_key_strs = [AssetUniqueKey.from_asset(asset).to_str() for asset in testing_assets]

        dag = SerializedDAG.to_dict(dag)
        actual = sorted(dag["dag"]["dag_dependencies"], key=lambda x: tuple(x.values()))
        expected = sorted(
            [
                {
                    "source": "test",
                    "target": "asset",
                    "label": "asset4",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[3],
                },
                {
                    "source": "external_dag_id",
                    "target": "test",
                    "label": "task1",
                    "dependency_type": "sensor",
                    "dependency_id": "task1",
                },
                {
                    "source": "test",
                    "target": "asset",
                    "label": "asset3",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[2],
                },
                {
                    "source": "test",
                    "target": "asset",
                    "label": "asset2",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[1],
                },
                {
                    "source": "asset",
                    "target": "test",
                    "label": "asset1",
                    "dependency_type": "asset",
                    "dependency_id": testing_asset_key_strs[0],
                },
            ],
            key=lambda x: tuple(x.values()),
        )
        assert actual == expected

    @pytest.mark.parametrize("mapped", [False, True])
    def test_derived_dag_deps_operator(self, mapped):
        """
        Tests DAG dependency detection for operators, including derived classes
        """
        from airflow.providers.standard.operators.empty import EmptyOperator
        from airflow.providers.standard.operators.trigger_dagrun import (
            TriggerDagRunOperator,
        )

        class DerivedOperator(TriggerDagRunOperator):
            pass

        logical_date = datetime(2020, 1, 1)
        for class_ in [TriggerDagRunOperator, DerivedOperator]:
            with DAG(
                dag_id="test_derived_dag_deps_trigger",
                schedule=None,
                start_date=logical_date,
            ) as dag:
                task1 = EmptyOperator(task_id="task1")
                if mapped:
                    task2 = class_.partial(
                        task_id="task2",
                        trigger_dag_id="trigger_dag_id",
                    ).expand(trigger_run_id=["one", "two"])
                else:
                    task2 = class_(
                        task_id="task2",
                        trigger_dag_id="trigger_dag_id",
                    )
                task1 >> task2

            dag = SerializedDAG.to_dict(dag)
            assert dag["dag"]["dag_dependencies"] == [
                {
                    "source": "test_derived_dag_deps_trigger",
                    "target": "trigger_dag_id",
                    "label": "task2",
                    "dependency_type": "trigger",
                    "dependency_id": "task2",
                }
            ]

    def test_task_group_sorted(self):
        """
        Tests serialize_task_group, make sure the list is in order
        """
        from airflow.providers.standard.operators.empty import EmptyOperator
        from airflow.serialization.serialized_objects import TaskGroupSerialization

        """
                    start
                    ╱  ╲
                  ╱      ╲
        task_group_up1  task_group_up2
            (task_up1)  (task_up2)
                 ╲       ╱
              task_group_middle
                (task_middle)
                  ╱      ╲
        task_group_down1 task_group_down2
           (task_down1) (task_down2)
                 ╲        ╱
                   ╲    ╱
                    end
        """
        logical_date = datetime(2020, 1, 1)
        with DAG(dag_id="test_task_group_sorted", schedule=None, start_date=logical_date) as dag:
            start = EmptyOperator(task_id="start")

            with TaskGroup("task_group_up1") as task_group_up1:
                _ = EmptyOperator(task_id="task_up1")

            with TaskGroup("task_group_up2") as task_group_up2:
                _ = EmptyOperator(task_id="task_up2")

            with TaskGroup("task_group_middle") as task_group_middle:
                _ = EmptyOperator(task_id="task_middle")

            with TaskGroup("task_group_down1") as task_group_down1:
                _ = EmptyOperator(task_id="task_down1")

            with TaskGroup("task_group_down2") as task_group_down2:
                _ = EmptyOperator(task_id="task_down2")

            end = EmptyOperator(task_id="end")

            start >> task_group_up1
            start >> task_group_up2
            task_group_up1 >> task_group_middle
            task_group_up2 >> task_group_middle
            task_group_middle >> task_group_down1
            task_group_middle >> task_group_down2
            task_group_down1 >> end
            task_group_down2 >> end

        task_group_middle_dict = TaskGroupSerialization.serialize_task_group(
            dag.task_group.children["task_group_middle"]
        )
        upstream_group_ids = task_group_middle_dict["upstream_group_ids"]
        assert upstream_group_ids == ["task_group_up1", "task_group_up2"]

        upstream_task_ids = task_group_middle_dict["upstream_task_ids"]
        assert upstream_task_ids == [
            "task_group_up1.task_up1",
            "task_group_up2.task_up2",
        ]

        downstream_group_ids = task_group_middle_dict["downstream_group_ids"]
        assert downstream_group_ids == ["task_group_down1", "task_group_down2"]

        task_group_down1_dict = TaskGroupSerialization.serialize_task_group(
            dag.task_group.children["task_group_down1"]
        )
        downstream_task_ids = task_group_down1_dict["downstream_task_ids"]
        assert downstream_task_ids == ["end"]

    def test_edge_info_serialization(self):
        """
        Tests edge_info serialization/deserialization.
        """
        from airflow.providers.standard.operators.empty import EmptyOperator
        from airflow.sdk import Label

        with DAG(
            "test_edge_info_serialization",
            schedule=None,
            start_date=datetime(2020, 1, 1),
        ) as dag:
            task1 = EmptyOperator(task_id="task1")
            task2 = EmptyOperator(task_id="task2")
            task1 >> Label("test label") >> task2

        dag_dict = SerializedDAG.to_dict(dag)
        SerializedDAG.validate_schema(dag_dict)
        json_dag = SerializedDAG.from_json(SerializedDAG.to_json(dag))
        self.validate_deserialized_dag(json_dag, dag)

        serialized_dag = SerializedDAG.deserialize_dag(SerializedDAG.serialize_dag(dag))

        assert serialized_dag.edge_info == dag.edge_info

    @pytest.mark.db_test
    @pytest.mark.parametrize("mode", ["poke", "reschedule"])
    def test_serialize_sensor(self, mode):
        from airflow.sdk.bases.sensor import BaseSensorOperator

        class DummySensor(BaseSensorOperator):
            def poke(self, context: Context):
                return False

        op = DummySensor(task_id="dummy", mode=mode, poke_interval=23)

        blob = SerializedBaseOperator.serialize_operator(op)
        assert "_is_sensor" in blob

        serialized_op = SerializedBaseOperator.deserialize_operator(blob)
        assert serialized_op.reschedule == (mode == "reschedule")
        assert ReadyToRescheduleDep in [type(d) for d in serialized_op.deps]

    @pytest.mark.parametrize("mode", ["poke", "reschedule"])
    def test_serialize_mapped_sensor_has_reschedule_dep(self, mode):
        from airflow.sdk.bases.sensor import BaseSensorOperator
        from airflow.ti_deps.deps.ready_to_reschedule import ReadyToRescheduleDep

        class DummySensor(BaseSensorOperator):
            def poke(self, context: Context):
                return False

        op = DummySensor.partial(task_id="dummy", mode=mode).expand(poke_interval=[23])

        blob = SerializedBaseOperator.serialize_mapped_operator(op)
        assert "_is_sensor" in blob
        assert "_is_mapped" in blob

        serialized_op = SerializedBaseOperator.deserialize_operator(blob)
        assert ReadyToRescheduleDep in [type(d) for d in serialized_op.deps]

    @pytest.mark.parametrize(
        "passed_success_callback, expected_value",
        [
            ({"on_success_callback": lambda x: print("hi")}, True),
            ({}, False),
        ],
    )
    def test_dag_on_success_callback_roundtrip(self, passed_success_callback, expected_value):
        """
        Test that when on_success_callback is passed to the DAG, has_on_success_callback is stored
        in Serialized JSON blob. And when it is de-serialized dag.has_on_success_callback is set to True.

        When the callback is not set, has_on_success_callback should not be stored in Serialized blob
        and so default to False on de-serialization
        """
        dag = DAG(
            dag_id="test_dag_on_success_callback_roundtrip",
            schedule=None,
            **passed_success_callback,
        )
        BaseOperator(task_id="simple_task", dag=dag, start_date=datetime(2019, 8, 1))

        serialized_dag = SerializedDAG.to_dict(dag)
        if expected_value:
            assert "has_on_success_callback" in serialized_dag["dag"]
        else:
            assert "has_on_success_callback" not in serialized_dag["dag"]

        deserialized_dag = SerializedDAG.from_dict(serialized_dag)

        assert deserialized_dag.has_on_success_callback is expected_value

    @pytest.mark.parametrize(
        "passed_failure_callback, expected_value",
        [
            ({"on_failure_callback": lambda x: print("hi")}, True),
            ({}, False),
        ],
    )
    def test_dag_on_failure_callback_roundtrip(self, passed_failure_callback, expected_value):
        """
        Test that when on_failure_callback is passed to the DAG, has_on_failure_callback is stored
        in Serialized JSON blob. And when it is de-serialized dag.has_on_failure_callback is set to True.

        When the callback is not set, has_on_failure_callback should not be stored in Serialized blob
        and so default to False on de-serialization
        """
        dag = DAG(
            dag_id="test_dag_on_failure_callback_roundtrip",
            schedule=None,
            **passed_failure_callback,
        )
        BaseOperator(task_id="simple_task", dag=dag, start_date=datetime(2019, 8, 1))

        serialized_dag = SerializedDAG.to_dict(dag)
        if expected_value:
            assert "has_on_failure_callback" in serialized_dag["dag"]
        else:
            assert "has_on_failure_callback" not in serialized_dag["dag"]

        deserialized_dag = SerializedDAG.from_dict(serialized_dag)

        assert deserialized_dag.has_on_failure_callback is expected_value

    @pytest.mark.parametrize(
        "dag_arg, conf_arg, expected",
        [
            (True, "True", True),
            (True, "False", True),
            (False, "True", False),
            (False, "False", False),
            (None, "True", True),
            (None, "False", False),
        ],
    )
    def test_dag_disable_bundle_versioning_roundtrip(self, dag_arg, conf_arg, expected):
        """
        Test that when disable_bundle_versioning is passed to the DAG, has_disable_bundle_versioning is stored
        in Serialized JSON blob. And when it is de-serialized dag.has_disable_bundle_versioning is set to True.

        When the callback is not set, has_disable_bundle_versioning should not be stored in Serialized blob
        and so default to False on de-serialization
        """
        with conf_vars({("dag_processor", "disable_bundle_versioning"): conf_arg}):
            kwargs = {}
            kwargs["disable_bundle_versioning"] = dag_arg
            dag = DAG(
                dag_id="test_dag_disable_bundle_versioning_roundtrip",
                schedule=None,
                **kwargs,
            )
            BaseOperator(task_id="simple_task", dag=dag, start_date=datetime(2019, 8, 1))
            serialized_dag = SerializedDAG.to_dict(dag)
            deserialized_dag = SerializedDAG.from_dict(serialized_dag)
            assert deserialized_dag.disable_bundle_versioning is expected

    @pytest.mark.parametrize(
        "object_to_serialized, expected_output",
        [
            (
                ["task_1", "task_5", "task_2", "task_4"],
                ["task_1", "task_5", "task_2", "task_4"],
            ),
            (
                {"task_1", "task_5", "task_2", "task_4"},
                ["task_1", "task_2", "task_4", "task_5"],
            ),
            (
                ("task_1", "task_5", "task_2", "task_4"),
                ["task_1", "task_5", "task_2", "task_4"],
            ),
            (
                {
                    "staging_schema": [
                        {"key:": "foo", "value": "bar"},
                        {"key:": "this", "value": "that"},
                        "test_conf",
                    ]
                },
                {
                    "staging_schema": [
                        {"__type": "dict", "__var": {"key:": "foo", "value": "bar"}},
                        {
                            "__type": "dict",
                            "__var": {"key:": "this", "value": "that"},
                        },
                        "test_conf",
                    ]
                },
            ),
            (
                {"task3": "test3", "task2": "test2", "task1": "test1"},
                {"task1": "test1", "task2": "test2", "task3": "test3"},
            ),
            (
                ("task_1", "task_5", "task_2", 3, ["x", "y"]),
                ["task_1", "task_5", "task_2", 3, ["x", "y"]],
            ),
        ],
    )
    def test_serialized_objects_are_sorted(self, object_to_serialized, expected_output):
        """Test Serialized Sets are sorted while list and tuple preserve order"""
        serialized_obj = SerializedDAG.serialize(object_to_serialized)
        if isinstance(serialized_obj, dict) and "__type" in serialized_obj:
            serialized_obj = serialized_obj["__var"]
        assert serialized_obj == expected_output

    def test_params_upgrade(self):
        """When pre-2.2.0 param (i.e. primitive) is deserialized we convert to Param"""
        serialized = {
            "__version": 2,
            "dag": {
                "dag_id": "simple_dag",
                "fileloc": "/path/to/file.py",
                "tasks": [],
                "timezone": "UTC",
                "params": {"none": None, "str": "str", "dict": {"a": "b"}},
            },
        }
        dag = SerializedDAG.from_dict(serialized)

        assert dag.params["none"] is None
        assert isinstance(dag.params.get_param("none"), Param)
        assert dag.params["str"] == "str"

    def test_params_serialization_from_dict_upgrade(self):
        """
        In <=2.9.2 params were serialized as a JSON object instead of a list of key-value pairs.
        This test asserts that the params are still deserialized properly.
        """
        serialized = {
            "__version": 2,
            "dag": {
                "dag_id": "simple_dag",
                "fileloc": "/path/to/file.py",
                "tasks": [],
                "timezone": "UTC",
                "params": {
                    "my_param": {
                        "__class": "airflow.models.param.Param",
                        "default": "str",
                    }
                },
            },
        }
        dag = SerializedDAG.from_dict(serialized)

        param = dag.params.get_param("my_param")
        assert isinstance(param, Param)
        assert param.value == "str"

    def test_params_serialize_default_2_2_0(self):
        """
        In 2.0.0, param ``default`` was assumed to be json-serializable objects and were not run though
        the standard serializer function.  In 2.2.2 we serialize param ``default``.  We keep this
        test only to ensure that params stored in 2.2.0 can still be parsed correctly.
        """
        serialized = {
            "__version": 2,
            "dag": {
                "dag_id": "simple_dag",
                "fileloc": "/path/to/file.py",
                "tasks": [],
                "timezone": "UTC",
                "params": [["str", {"__class": "airflow.models.param.Param", "default": "str"}]],
            },
        }
        SerializedDAG.validate_schema(serialized)
        dag = SerializedDAG.from_dict(serialized)

        assert isinstance(dag.params.get_param("str"), Param)
        assert dag.params["str"] == "str"

    def test_params_serialize_default(self):
        serialized = {
            "__version": 2,
            "dag": {
                "dag_id": "simple_dag",
                "fileloc": "/path/to/file.py",
                "tasks": [],
                "timezone": "UTC",
                "params": [
                    [
                        "my_param",
                        {
                            "default": "a string value",
                            "description": "hello",
                            "schema": {"__var": {"type": "string"}, "__type": "dict"},
                            "__class": "airflow.models.param.Param",
                        },
                    ]
                ],
            },
        }
        SerializedDAG.validate_schema(serialized)
        dag = SerializedDAG.from_dict(serialized)

        assert dag.params["my_param"] == "a string value"
        param = dag.params.get_param("my_param")
        assert isinstance(param, Param)
        assert param.description == "hello"
        assert param.schema == {"type": "string"}

    @pytest.mark.db_test
    def test_not_templateable_fields_in_serialized_dag(self):
        """
        Test that when we use not templateable fields, an Airflow exception is raised.
        """

        class TestOperator(BaseOperator):
            template_fields = (
                "execution_timeout",  # not templateable
                "run_as_user",  # templateable
            )

            def execute(self, context: Context):
                pass

        dag = DAG(dag_id="test_dag", schedule=None, start_date=datetime(2023, 11, 9))

        with dag:
            task = TestOperator(
                task_id="test_task",
                run_as_user="{{ test_run_as_user }}",
                execution_timeout=timedelta(seconds=10),
            )
            task.render_template_fields(context={"test_run_as_user": "foo"})
            assert task.run_as_user == "foo"

        with pytest.raises(
            AirflowException,
            match=re.escape(
                dedent(
                    """Failed to serialize DAG 'test_dag': Cannot template BaseOperator field:
                        'execution_timeout' op.__class__.__name__='TestOperator' op.template_fields=('execution_timeout', 'run_as_user')"""
                )
            ),
        ):
            SerializedDAG.to_dict(dag)

    @pytest.mark.db_test
    def test_start_trigger_args_in_serialized_dag(self):
        """
        Test that when we provide start_trigger_args, the DAG can be correctly serialized.
        """

        class TestOperator(BaseOperator):
            start_trigger_args = StartTriggerArgs(
                trigger_cls="airflow.providers.standard.triggers.temporal.TimeDeltaTrigger",
                trigger_kwargs={"delta": timedelta(seconds=1)},
                next_method="execute_complete",
                next_kwargs=None,
                timeout=None,
            )
            start_from_trigger = False

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.start_trigger_args.trigger_kwargs = {"delta": timedelta(seconds=2)}
                self.start_from_trigger = True

            def execute_complete(self):
                pass

        class Test2Operator(BaseOperator):
            start_trigger_args = StartTriggerArgs(
                trigger_cls="airflow.triggers.testing.SuccessTrigger",
                trigger_kwargs={},
                next_method="execute_complete",
                next_kwargs=None,
                timeout=None,
            )
            start_from_trigger = True

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

            def execute_complete(self):
                pass

        dag = DAG(dag_id="test_dag", schedule=None, start_date=datetime(2023, 11, 9))

        with dag:
            TestOperator(task_id="test_task_1")
            Test2Operator(task_id="test_task_2")

        serialized_obj = SerializedDAG.to_dict(dag)
        tasks = serialized_obj["dag"]["tasks"]

        assert tasks[0]["__var"]["start_trigger_args"] == {
            "__type": "START_TRIGGER_ARGS",
            "trigger_cls": "airflow.providers.standard.triggers.temporal.TimeDeltaTrigger",
            # "trigger_kwargs": {"__type": "dict", "__var": {"delta": {"__type": "timedelta", "__var": 2.0}}},
            "trigger_kwargs": {
                "__type": "dict",
                "__var": {"delta": {"__type": "timedelta", "__var": 2.0}},
            },
            "next_method": "execute_complete",
            "next_kwargs": None,
            "timeout": None,
        }
        assert tasks[0]["__var"]["start_from_trigger"] is True

        assert tasks[1]["__var"]["start_trigger_args"] == {
            "__type": "START_TRIGGER_ARGS",
            "trigger_cls": "airflow.triggers.testing.SuccessTrigger",
            "trigger_kwargs": {"__type": "dict", "__var": {}},
            "next_method": "execute_complete",
            "next_kwargs": None,
            "timeout": None,
        }
        assert tasks[1]["__var"]["start_from_trigger"] is True


def test_kubernetes_optional():
    """Serialisation / deserialisation continues to work without kubernetes installed"""

    def mock__import__(name, globals_=None, locals_=None, fromlist=(), level=0):
        if level == 0 and name.partition(".")[0] == "kubernetes":
            raise ImportError("No module named 'kubernetes'")
        return importlib.__import__(name, globals=globals_, locals=locals_, fromlist=fromlist, level=level)

    with mock.patch("builtins.__import__", side_effect=mock__import__) as import_mock:
        # load module from scratch, this does not replace any already imported
        # airflow.serialization.serialized_objects module in sys.modules
        spec = importlib.util.find_spec("airflow.serialization.serialized_objects")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        # if we got this far, the module did not try to load kubernetes, but
        # did it try to access airflow.providers.cncf.kubernetes.*?
        imported_airflow = {
            c.args[0].split(".", 2)[1] for c in import_mock.call_args_list if c.args[0].startswith("airflow.")
        }
        assert "kubernetes" not in imported_airflow

        # pod loading is not supported when kubernetes is not available
        pod_override = {
            "__type": "k8s.V1Pod",
            "__var": PodGenerator.serialize_pod(executor_config_pod),
        }

        with pytest.raises(RuntimeError):
            module.BaseSerialization.from_dict(pod_override)

        # basic serialization should succeed
        module.SerializedDAG.to_dict(make_simple_dag())


def test_operator_expand_serde():
    literal = [1, 2, {"a": "b"}]
    real_op = BashOperator.partial(task_id="a", executor_config={"dict": {"sub": "value"}}).expand(
        bash_command=literal
    )

    serialized = BaseSerialization.serialize(real_op)

    assert serialized["__var"] == {
        "_is_mapped": True,
        "_task_module": "airflow.providers.standard.operators.bash",
        "task_type": "BashOperator",
        "start_trigger_args": None,
        "start_from_trigger": False,
        "downstream_task_ids": [],
        "expand_input": {
            "type": "dict-of-lists",
            "value": {
                "__type": "dict",
                "__var": {"bash_command": [1, 2, {"__type": "dict", "__var": {"a": "b"}}]},
            },
        },
        "partial_kwargs": {
            "executor_config": {
                "__type": "dict",
                "__var": {"dict": {"__type": "dict", "__var": {"sub": "value"}}},
            },
        },
        "task_id": "a",
        "operator_extra_links": [],
        "template_fields": ["bash_command", "env", "cwd"],
        "template_ext": [".sh", ".bash"],
        "template_fields_renderers": {"bash_command": "bash", "env": "json"},
        "ui_color": "#f0ede4",
        "ui_fgcolor": "#000",
        "_disallow_kwargs_override": False,
        "_expand_input_attr": "expand_input",
    }

    op = BaseSerialization.deserialize(serialized)
    assert isinstance(op, MappedOperator)

    assert op.operator_class == {
        "task_type": "BashOperator",
        "start_trigger_args": None,
        "start_from_trigger": False,
        "downstream_task_ids": [],
        "task_id": "a",
        "template_ext": [".sh", ".bash"],
        "template_fields": ["bash_command", "env", "cwd"],
        "template_fields_renderers": {"bash_command": "bash", "env": "json"},
        "ui_color": "#f0ede4",
        "ui_fgcolor": "#000",
    }
    assert op.expand_input.value["bash_command"] == literal
    assert op.partial_kwargs["executor_config"] == {"dict": {"sub": "value"}}


def test_operator_expand_xcomarg_serde():
    from airflow.models.xcom_arg import SchedulerPlainXComArg
    from airflow.sdk.definitions.xcom_arg import XComArg
    from airflow.serialization.serialized_objects import _XComRef

    with DAG("test-dag", schedule=None, start_date=datetime(2020, 1, 1)) as dag:
        task1 = BaseOperator(task_id="op1")
        mapped = MockOperator.partial(task_id="task_2").expand(arg2=XComArg(task1))

    serialized = BaseSerialization.serialize(mapped)
    assert serialized["__var"] == {
        "_is_mapped": True,
        "_task_module": "tests_common.test_utils.mock_operators",
        "task_type": "MockOperator",
        "downstream_task_ids": [],
        "expand_input": {
            "type": "dict-of-lists",
            "value": {
                "__type": "dict",
                "__var": {
                    "arg2": {
                        "__type": "xcomref",
                        "__var": {"task_id": "op1", "key": "return_value"},
                    }
                },
            },
        },
        "partial_kwargs": {},
        "task_id": "task_2",
        "template_fields": ["arg1", "arg2"],
        "template_ext": [],
        "template_fields_renderers": {},
        "operator_extra_links": [],
        "ui_color": "#fff",
        "ui_fgcolor": "#000",
        "_disallow_kwargs_override": False,
        "_expand_input_attr": "expand_input",
        "start_trigger_args": None,
        "start_from_trigger": False,
    }

    op = BaseSerialization.deserialize(serialized)

    # The XComArg can't be deserialized before the DAG is.
    xcom_ref = op.expand_input.value["arg2"]
    assert xcom_ref == _XComRef({"task_id": "op1", "key": XCOM_RETURN_KEY})

    serialized_dag: DAG = SerializedDAG.from_dict(SerializedDAG.to_dict(dag))

    xcom_arg = serialized_dag.task_dict["task_2"].expand_input.value["arg2"]
    assert isinstance(xcom_arg, SchedulerPlainXComArg)
    assert xcom_arg.operator is serialized_dag.task_dict["op1"]


@pytest.mark.parametrize("strict", [True, False])
def test_operator_expand_kwargs_literal_serde(strict):
    from airflow.sdk.definitions.xcom_arg import XComArg
    from airflow.serialization.serialized_objects import DEFAULT_OPERATOR_DEPS, _XComRef

    with DAG("test-dag", schedule=None, start_date=datetime(2020, 1, 1)) as dag:
        task1 = BaseOperator(task_id="op1")
        mapped = MockOperator.partial(task_id="task_2").expand_kwargs(
            [{"a": "x"}, {"a": XComArg(task1)}],
            strict=strict,
        )

    serialized = BaseSerialization.serialize(mapped)
    assert serialized["__var"] == {
        "_is_mapped": True,
        "_task_module": "tests_common.test_utils.mock_operators",
        "task_type": "MockOperator",
        "downstream_task_ids": [],
        "expand_input": {
            "type": "list-of-dicts",
            "value": [
                {"__type": "dict", "__var": {"a": "x"}},
                {
                    "__type": "dict",
                    "__var": {
                        "a": {
                            "__type": "xcomref",
                            "__var": {"task_id": "op1", "key": "return_value"},
                        }
                    },
                },
            ],
        },
        "partial_kwargs": {},
        "task_id": "task_2",
        "template_fields": ["arg1", "arg2"],
        "template_ext": [],
        "template_fields_renderers": {},
        "operator_extra_links": [],
        "ui_color": "#fff",
        "ui_fgcolor": "#000",
        "_disallow_kwargs_override": strict,
        "_expand_input_attr": "expand_input",
        "start_trigger_args": None,
        "start_from_trigger": False,
    }

    op = BaseSerialization.deserialize(serialized)
    assert op.deps == DEFAULT_OPERATOR_DEPS
    assert op._disallow_kwargs_override == strict

    # The XComArg can't be deserialized before the DAG is.
    expand_value = op.expand_input.value
    assert expand_value == [
        {"a": "x"},
        {"a": _XComRef({"task_id": "op1", "key": XCOM_RETURN_KEY})},
    ]

    serialized_dag: DAG = SerializedDAG.from_dict(SerializedDAG.to_dict(dag))

    resolved_expand_value = serialized_dag.task_dict["task_2"].expand_input.value
    assert resolved_expand_value == [
        {"a": "x"},
        {"a": _XComRef({"task_id": "op1", "key": "return_value"})},
    ]


@pytest.mark.parametrize("strict", [True, False])
def test_operator_expand_kwargs_xcomarg_serde(strict):
    from airflow.models.xcom_arg import SchedulerPlainXComArg
    from airflow.sdk.definitions.xcom_arg import XComArg
    from airflow.serialization.serialized_objects import _XComRef

    with DAG("test-dag", schedule=None, start_date=datetime(2020, 1, 1)) as dag:
        task1 = BaseOperator(task_id="op1")
        mapped = MockOperator.partial(task_id="task_2").expand_kwargs(XComArg(task1), strict=strict)

    serialized = SerializedBaseOperator.serialize(mapped)
    assert serialized["__var"] == {
        "_is_mapped": True,
        "_task_module": "tests_common.test_utils.mock_operators",
        "task_type": "MockOperator",
        "downstream_task_ids": [],
        "expand_input": {
            "type": "list-of-dicts",
            "value": {
                "__type": "xcomref",
                "__var": {"task_id": "op1", "key": "return_value"},
            },
        },
        "partial_kwargs": {},
        "task_id": "task_2",
        "template_fields": ["arg1", "arg2"],
        "template_ext": [],
        "template_fields_renderers": {},
        "operator_extra_links": [],
        "ui_color": "#fff",
        "ui_fgcolor": "#000",
        "_disallow_kwargs_override": strict,
        "_expand_input_attr": "expand_input",
        "start_trigger_args": None,
        "start_from_trigger": False,
    }

    op = BaseSerialization.deserialize(serialized)
    assert op._disallow_kwargs_override == strict

    # The XComArg can't be deserialized before the DAG is.
    xcom_ref = op.expand_input.value
    assert xcom_ref == _XComRef({"task_id": "op1", "key": XCOM_RETURN_KEY})

    serialized_dag: DAG = SerializedDAG.from_dict(SerializedDAG.to_dict(dag))

    xcom_arg = serialized_dag.task_dict["task_2"].expand_input.value
    assert isinstance(xcom_arg, SchedulerPlainXComArg)
    assert xcom_arg.operator is serialized_dag.task_dict["op1"]


def test_operator_expand_deserialized_unmap():
    """Unmap a deserialized mapped operator should be similar to deserializing an non-mapped operator."""
    normal = BashOperator(task_id="a", bash_command=[1, 2], executor_config={"a": "b"})
    mapped = BashOperator.partial(task_id="a", executor_config={"a": "b"}).expand(bash_command=[1, 2])

    ser_mapped = BaseSerialization.serialize(mapped)
    deser_mapped = BaseSerialization.deserialize(ser_mapped)
    deser_mapped.dag = None

    ser_normal = BaseSerialization.serialize(normal)
    deser_normal = BaseSerialization.deserialize(ser_normal)
    deser_normal.dag = None
    unmapped_deser_mapped = deser_mapped.unmap(None)

    assert type(unmapped_deser_mapped) is type(deser_normal) is SerializedBaseOperator
    assert unmapped_deser_mapped.task_id == deser_normal.task_id == "a"
    assert unmapped_deser_mapped.executor_config == deser_normal.executor_config == {"a": "b"}


@pytest.mark.db_test
def test_sensor_expand_deserialized_unmap():
    """Unmap a deserialized mapped sensor should be similar to deserializing a non-mapped sensor"""
    dag = DAG(dag_id="hello", schedule=None, start_date=None)
    with dag:
        normal = BashSensor(task_id="a", bash_command=[1, 2], mode="reschedule")
        mapped = BashSensor.partial(task_id="b", mode="reschedule").expand(bash_command=[1, 2])
    ser_mapped = SerializedBaseOperator.serialize(mapped)
    deser_mapped = SerializedBaseOperator.deserialize(ser_mapped)
    deser_mapped.dag = dag
    deser_unmapped = deser_mapped.unmap(None)
    ser_normal = SerializedBaseOperator.serialize(normal)
    deser_normal = SerializedBaseOperator.deserialize(ser_normal)
    comps = set(BashSensor._comps)
    comps.remove("task_id")
    comps.remove("dag_id")
    assert all(getattr(deser_unmapped, c, None) == getattr(deser_normal, c, None) for c in comps)


def test_task_resources_serde():
    """
    Test task resources serialization/deserialization.
    """
    from airflow.providers.standard.operators.empty import EmptyOperator

    logical_date = datetime(2020, 1, 1)
    task_id = "task1"
    with DAG("test_task_resources", schedule=None, start_date=logical_date) as _:
        task = EmptyOperator(task_id=task_id, resources={"cpus": 0.1, "ram": 2048})

    serialized = BaseSerialization.serialize(task)
    assert serialized["__var"]["resources"] == {
        "cpus": {"name": "CPU", "qty": 0.1, "units_str": "core(s)"},
        "disk": {"name": "Disk", "qty": 512, "units_str": "MB"},
        "gpus": {"name": "GPU", "qty": 0, "units_str": "gpu(s)"},
        "ram": {"name": "RAM", "qty": 2048, "units_str": "MB"},
    }


@pytest.fixture(params=[None, timedelta(hours=1)])
def default_task_execution_timeout(request):
    """
    Mock setting core.default_task_execution_timeout in airflow.cfg.
    """
    from airflow.serialization.serialized_objects import SerializedBaseOperator

    DEFAULT_TASK_EXECUTION_TIMEOUT = request.param
    with mock.patch.dict(
        SerializedBaseOperator._CONSTRUCTOR_PARAMS, {"execution_timeout": DEFAULT_TASK_EXECUTION_TIMEOUT}
    ):
        yield DEFAULT_TASK_EXECUTION_TIMEOUT


@pytest.mark.parametrize("execution_timeout", [None, timedelta(hours=1)])
def test_task_execution_timeout_serde(execution_timeout, default_task_execution_timeout):
    """
    Test task execution_timeout serialization/deserialization.
    """
    from airflow.providers.standard.operators.empty import EmptyOperator

    with DAG("test_task_execution_timeout", schedule=None, start_date=datetime(2020, 1, 1)) as _:
        task = EmptyOperator(task_id="task1", execution_timeout=execution_timeout)

    serialized = BaseSerialization.serialize(task)
    if execution_timeout != default_task_execution_timeout:
        assert "execution_timeout" in serialized["__var"]

    deserialized = BaseSerialization.deserialize(serialized)
    assert deserialized.execution_timeout == task.execution_timeout


def test_taskflow_expand_serde():
    from airflow.models.xcom_arg import XComArg
    from airflow.sdk import task
    from airflow.serialization.serialized_objects import _ExpandInputRef, _XComRef

    with DAG("test-dag", schedule=None, start_date=datetime(2020, 1, 1)) as dag:
        op1 = BaseOperator(task_id="op1")

        @task(retry_delay=30)
        def x(arg1, arg2, arg3):
            print(arg1, arg2, arg3)

        print("**", type(x), type(x.partial), type(x.expand))
        x.partial(arg1=[1, 2, {"a": "b"}]).expand(arg2={"a": 1, "b": 2}, arg3=XComArg(op1))

    original = dag.get_task("x")

    serialized = BaseSerialization.serialize(original)
    assert serialized["__var"] == {
        "_is_mapped": True,
        "_task_module": "airflow.providers.standard.decorators.python",
        "task_type": "_PythonDecoratedOperator",
        "_operator_name": "@task",
        "downstream_task_ids": [],
        "partial_kwargs": {
            "is_setup": False,
            "is_teardown": False,
            "on_failure_fail_dagrun": False,
            "op_args": [],
            "op_kwargs": {
                "__type": "dict",
                "__var": {"arg1": [1, 2, {"__type": "dict", "__var": {"a": "b"}}]},
            },
            "retry_delay": {"__type": "timedelta", "__var": 30.0},
        },
        "op_kwargs_expand_input": {
            "type": "dict-of-lists",
            "value": {
                "__type": "dict",
                "__var": {
                    "arg2": {"__type": "dict", "__var": {"a": 1, "b": 2}},
                    "arg3": {
                        "__type": "xcomref",
                        "__var": {"task_id": "op1", "key": "return_value"},
                    },
                },
            },
        },
        "operator_extra_links": [],
        "ui_color": "#ffefeb",
        "ui_fgcolor": "#000",
        "task_id": "x",
        "template_ext": [],
        "template_fields": ["templates_dict", "op_args", "op_kwargs"],
        "template_fields_renderers": {
            "templates_dict": "json",
            "op_args": "py",
            "op_kwargs": "py",
        },
        "_disallow_kwargs_override": False,
        "_expand_input_attr": "op_kwargs_expand_input",
        "python_callable_name": qualname(x),
        "start_trigger_args": None,
        "start_from_trigger": False,
    }

    deserialized = BaseSerialization.deserialize(serialized)
    assert isinstance(deserialized, MappedOperator)
    assert deserialized.upstream_task_ids == set()
    assert deserialized.downstream_task_ids == set()

    assert deserialized.op_kwargs_expand_input == _ExpandInputRef(
        key="dict-of-lists",
        value={
            "arg2": {"a": 1, "b": 2},
            "arg3": _XComRef({"task_id": "op1", "key": XCOM_RETURN_KEY}),
        },
    )
    assert deserialized.partial_kwargs == {
        "is_setup": False,
        "is_teardown": False,
        "on_failure_fail_dagrun": False,
        "op_args": [],
        "op_kwargs": {"arg1": [1, 2, {"a": "b"}]},
        "retry_delay": timedelta(seconds=30),
    }

    # this dag is not pickleable in this context, so we have to simply
    # set it to None
    deserialized.dag = None

    # Ensure the serialized operator can also be correctly pickled, to ensure
    # correct interaction between DAG pickling and serialization. This is done
    # here so we don't need to duplicate tests between pickled and non-pickled
    # DAGs everywhere else.
    pickled = pickle.loads(pickle.dumps(deserialized))
    assert pickled.op_kwargs_expand_input == _ExpandInputRef(
        key="dict-of-lists",
        value={
            "arg2": {"a": 1, "b": 2},
            "arg3": _XComRef({"task_id": "op1", "key": XCOM_RETURN_KEY}),
        },
    )
    assert pickled.partial_kwargs == {
        "is_setup": False,
        "is_teardown": False,
        "on_failure_fail_dagrun": False,
        "op_args": [],
        "op_kwargs": {"arg1": [1, 2, {"a": "b"}]},
        "retry_delay": timedelta(seconds=30),
    }


@pytest.mark.parametrize("strict", [True, False])
def test_taskflow_expand_kwargs_serde(strict):
    from airflow.models.xcom_arg import XComArg
    from airflow.sdk import task
    from airflow.serialization.serialized_objects import _ExpandInputRef, _XComRef

    with DAG("test-dag", schedule=None, start_date=datetime(2020, 1, 1)) as dag:
        op1 = BaseOperator(task_id="op1")

        @task(retry_delay=30)
        def x(arg1, arg2, arg3):
            print(arg1, arg2, arg3)

        x.partial(arg1=[1, 2, {"a": "b"}]).expand_kwargs(XComArg(op1), strict=strict)

    original = dag.get_task("x")

    serialized = BaseSerialization.serialize(original)
    assert serialized["__var"] == {
        "_is_mapped": True,
        "_task_module": "airflow.providers.standard.decorators.python",
        "task_type": "_PythonDecoratedOperator",
        "_operator_name": "@task",
        "python_callable_name": qualname(x),
        "start_trigger_args": None,
        "start_from_trigger": False,
        "downstream_task_ids": [],
        "partial_kwargs": {
            "is_setup": False,
            "is_teardown": False,
            "on_failure_fail_dagrun": False,
            "op_args": [],
            "op_kwargs": {
                "__type": "dict",
                "__var": {"arg1": [1, 2, {"__type": "dict", "__var": {"a": "b"}}]},
            },
            "retry_delay": {"__type": "timedelta", "__var": 30.0},
        },
        "op_kwargs_expand_input": {
            "type": "list-of-dicts",
            "value": {
                "__type": "xcomref",
                "__var": {"task_id": "op1", "key": "return_value"},
            },
        },
        "operator_extra_links": [],
        "ui_color": "#ffefeb",
        "ui_fgcolor": "#000",
        "task_id": "x",
        "template_ext": [],
        "template_fields": ["templates_dict", "op_args", "op_kwargs"],
        "template_fields_renderers": {
            "templates_dict": "json",
            "op_args": "py",
            "op_kwargs": "py",
        },
        "_disallow_kwargs_override": strict,
        "_expand_input_attr": "op_kwargs_expand_input",
    }

    deserialized = BaseSerialization.deserialize(serialized)
    assert isinstance(deserialized, MappedOperator)
    assert deserialized._disallow_kwargs_override == strict
    assert deserialized.upstream_task_ids == set()
    assert deserialized.downstream_task_ids == set()

    assert deserialized.op_kwargs_expand_input == _ExpandInputRef(
        key="list-of-dicts",
        value=_XComRef({"task_id": "op1", "key": XCOM_RETURN_KEY}),
    )
    assert deserialized.partial_kwargs == {
        "is_setup": False,
        "is_teardown": False,
        "on_failure_fail_dagrun": False,
        "op_args": [],
        "op_kwargs": {"arg1": [1, 2, {"a": "b"}]},
        "retry_delay": timedelta(seconds=30),
    }

    # this dag is not pickleable in this context, so we have to simply
    # set it to None
    deserialized.dag = None

    # Ensure the serialized operator can also be correctly pickled, to ensure
    # correct interaction between DAG pickling and serialization. This is done
    # here so we don't need to duplicate tests between pickled and non-pickled
    # DAGs everywhere else.
    pickled = pickle.loads(pickle.dumps(deserialized))
    assert pickled.op_kwargs_expand_input == _ExpandInputRef(
        "list-of-dicts",
        _XComRef({"task_id": "op1", "key": XCOM_RETURN_KEY}),
    )
    assert pickled.partial_kwargs == {
        "is_setup": False,
        "is_teardown": False,
        "on_failure_fail_dagrun": False,
        "op_args": [],
        "op_kwargs": {"arg1": [1, 2, {"a": "b"}]},
        "retry_delay": timedelta(seconds=30),
    }


def test_mapped_task_group_serde():
    from airflow.models.expandinput import SchedulerDictOfListsExpandInput
    from airflow.sdk.definitions.decorators.task_group import task_group
    from airflow.sdk.definitions.taskgroup import MappedTaskGroup

    with DAG("test-dag", schedule=None, start_date=datetime(2020, 1, 1)) as dag:

        @task_group
        def tg(a: str) -> None:
            BaseOperator(task_id="op1")
            with pytest.raises(NotImplementedError) as ctx:
                BashOperator.partial(task_id="op2").expand(bash_command=["ls", a])
            assert str(ctx.value) == "operator expansion in an expanded task group is not yet supported"

        tg.expand(a=[".", ".."])

    ser_dag = SerializedBaseOperator.serialize(dag)
    assert ser_dag[Encoding.VAR]["task_group"]["children"]["tg"] == (
        "taskgroup",
        {
            "_group_id": "tg",
            "children": {
                "tg.op1": ("operator", "tg.op1"),
                # "tg.op2": ("operator", "tg.op2"),
            },
            "downstream_group_ids": [],
            "downstream_task_ids": [],
            "expand_input": {
                "type": "dict-of-lists",
                "value": {"__type": "dict", "__var": {"a": [".", ".."]}},
            },
            "group_display_name": "",
            "is_mapped": True,
            "prefix_group_id": True,
            "tooltip": "",
            "ui_color": "CornflowerBlue",
            "ui_fgcolor": "#000",
            "upstream_group_ids": [],
            "upstream_task_ids": [],
        },
    )

    serde_dag = SerializedDAG.deserialize_dag(ser_dag[Encoding.VAR])
    serde_tg = serde_dag.task_group.children["tg"]
    assert isinstance(serde_tg, MappedTaskGroup)
    assert serde_tg._expand_input == SchedulerDictOfListsExpandInput({"a": [".", ".."]})


@pytest.mark.db_test
def test_mapped_task_with_operator_extra_links_property():
    class _DummyOperator(BaseOperator):
        def __init__(self, inputs, **kwargs):
            super().__init__(**kwargs)
            self.inputs = inputs

        @property
        def operator_extra_links(self):
            return (AirflowLink2(),)

    with DAG("test-dag", schedule=None, start_date=datetime(2020, 1, 1)) as dag:
        _DummyOperator.partial(task_id="task").expand(inputs=[1, 2, 3])
    serialized_dag = SerializedBaseOperator.serialize(dag)
    assert serialized_dag[Encoding.VAR]["tasks"][0]["__var"] == {
        "task_id": "task",
        "expand_input": {
            "type": "dict-of-lists",
            "value": {"__type": "dict", "__var": {"inputs": [1, 2, 3]}},
        },
        "partial_kwargs": {},
        "_disallow_kwargs_override": False,
        "_expand_input_attr": "expand_input",
        "downstream_task_ids": [],
        "_operator_extra_links": {"airflow": "_link_AirflowLink2"},
        "ui_color": "#fff",
        "ui_fgcolor": "#000",
        "template_ext": [],
        "template_fields": [],
        "template_fields_renderers": {},
        "task_type": "_DummyOperator",
        "_task_module": "unit.serialization.test_dag_serialization",
        "_is_mapped": True,
        "start_trigger_args": None,
        "start_from_trigger": False,
    }
    deserialized_dag = SerializedDAG.deserialize_dag(serialized_dag[Encoding.VAR])
    # operator defined links have to be instances of XComOperatorLink
    assert deserialized_dag.task_dict["task"].operator_extra_links == [
        XComOperatorLink(name="airflow", xcom_key="_link_AirflowLink2")
    ]

    mapped_task = deserialized_dag.task_dict["task"]
    assert mapped_task.operator_extra_link_dict == {
        "airflow": XComOperatorLink(name="airflow", xcom_key="_link_AirflowLink2")
    }
    assert mapped_task.global_operator_extra_link_dict == {"airflow": AirflowLink(), "github": GithubLink()}
    assert mapped_task.extra_links == sorted({"airflow", "github"})


def test_handle_v1_serdag():
    v1 = {
        "__version": 1,
        "dag": {
            "default_args": {
                "__type": "dict",
                "__var": {
                    "depends_on_past": False,
                    "retries": 1,
                    "retry_delay": {"__type": "timedelta", "__var": 300.0},
                    "max_retry_delay": {"__type": "timedelta", "__var": 600.0},
                    "sla": {"__type": "timedelta", "__var": 100.0},
                },
            },
            "start_date": 1564617600.0,
            "_task_group": {
                "_group_id": None,
                "prefix_group_id": True,
                "children": {
                    "bash_task": ("operator", "bash_task"),
                    "custom_task": ("operator", "custom_task"),
                },
                "tooltip": "",
                "ui_color": "CornflowerBlue",
                "ui_fgcolor": "#000",
                "upstream_group_ids": [],
                "downstream_group_ids": [],
                "upstream_task_ids": [],
                "downstream_task_ids": [],
            },
            "is_paused_upon_creation": False,
            "_dag_id": "simple_dag",
            "deadline": None,
            "doc_md": "### DAG Tutorial Documentation",
            "fileloc": None,
            "_processor_dags_folder": (
                AIRFLOW_REPO_ROOT_PATH / "airflow-core" / "tests" / "unit" / "dags"
            ).as_posix(),
            "tasks": [
                {
                    "__type": "operator",
                    "__var": {
                        "task_id": "bash_task",
                        "retries": 1,
                        "retry_delay": 300.0,
                        "max_retry_delay": 600.0,
                        "sla": 100.0,
                        "downstream_task_ids": [],
                        "ui_color": "#f0ede4",
                        "ui_fgcolor": "#000",
                        "template_ext": [".sh", ".bash"],
                        "template_fields": ["bash_command", "env", "cwd"],
                        "template_fields_renderers": {"bash_command": "bash", "env": "json"},
                        "bash_command": "echo {{ task.task_id }}",
                        "_task_type": "BashOperator",
                        # Slightly difference from v2-10-stable here, we manually changed this path
                        "_task_module": "airflow.providers.standard.operators.bash",
                        "owner": "airflow",
                        "pool": "default_pool",
                        "is_setup": False,
                        "is_teardown": False,
                        "on_failure_fail_dagrun": False,
                        "executor_config": {
                            "__type": "dict",
                            "__var": {
                                "pod_override": {
                                    "__type": "k8s.V1Pod",
                                    "__var": PodGenerator.serialize_pod(executor_config_pod),
                                }
                            },
                        },
                        "doc_md": "### Task Tutorial Documentation",
                        "_log_config_logger_name": "airflow.task.operators",
                        "_needs_expansion": False,
                        "weight_rule": "downstream",
                        "start_trigger_args": None,
                        "start_from_trigger": False,
                        "inlets": [
                            {
                                "__type": "dataset",
                                "__var": {
                                    "extra": {},
                                    "uri": "asset-1",
                                },
                            },
                            {
                                "__type": "dataset_alias",
                                "__var": {"name": "alias-name"},
                            },
                        ],
                        "outlets": [
                            {
                                "__type": "dataset",
                                "__var": {
                                    "extra": {},
                                    "uri": "asset-2",
                                },
                            },
                        ],
                    },
                },
                {
                    "__type": "operator",
                    "__var": {
                        "task_id": "custom_task",
                        "retries": 1,
                        "retry_delay": 300.0,
                        "max_retry_delay": 600.0,
                        "sla": 100.0,
                        "downstream_task_ids": [],
                        "_operator_extra_links": [{"tests.test_utils.mock_operators.CustomOpLink": {}}],
                        "ui_color": "#fff",
                        "ui_fgcolor": "#000",
                        "template_ext": [],
                        "template_fields": ["bash_command"],
                        "template_fields_renderers": {},
                        "_task_type": "CustomOperator",
                        "_operator_name": "@custom",
                        # Slightly difference from v2-10-stable here, we manually changed this path
                        "_task_module": "tests_common.test_utils.mock_operators",
                        "pool": "default_pool",
                        "is_setup": False,
                        "is_teardown": False,
                        "on_failure_fail_dagrun": False,
                        "_log_config_logger_name": "airflow.task.operators",
                        "_needs_expansion": False,
                        "weight_rule": "downstream",
                        "start_trigger_args": None,
                        "start_from_trigger": False,
                    },
                },
            ],
            "schedule_interval": {"__type": "timedelta", "__var": 86400.0},
            "timezone": "UTC",
            "_access_control": {
                "__type": "dict",
                "__var": {
                    "test_role": {
                        "__type": "dict",
                        "__var": {
                            "DAGs": {
                                "__type": "set",
                                "__var": [permissions.ACTION_CAN_READ, permissions.ACTION_CAN_EDIT],
                            }
                        },
                    }
                },
            },
            "edge_info": {},
            "dag_dependencies": [
                # dataset as schedule (source)
                {
                    "source": "dataset",
                    "target": "dag1",
                    "dependency_type": "dataset",
                    "dependency_id": "dataset_uri_1",
                },
                # dataset alias (resolved) as schedule (source)
                {
                    "source": "dataset",
                    "target": "dataset-alias:alias_name_1",
                    "dependency_type": "dataset",
                    "dependency_id": "dataset_uri_2",
                },
                {
                    "source": "dataset:alias_name_1",
                    "target": "dag2",
                    "dependency_type": "dataset-alias",
                    "dependency_id": "alias_name_1",
                },
                # dataset alias (not resolved) as schedule (source)
                {
                    "source": "dataset-alias",
                    "target": "dag2",
                    "dependency_type": "dataset-alias",
                    "dependency_id": "alias_name_2",
                },
                # dataset as outlets (target)
                {
                    "source": "dag10",
                    "target": "dataset",
                    "dependency_type": "dataset",
                    "dependency_id": "dataset_uri_10",
                },
                # dataset alias (resolved) as outlets (target)
                {
                    "source": "dag20",
                    "target": "dataset-alias:alias_name_10",
                    "dependency_type": "dataset",
                    "dependency_id": "dataset_uri_20",
                },
                {
                    "source": "dataset:dataset_uri_20",
                    "target": "dataset-alias",
                    "dependency_type": "dataset-alias",
                    "dependency_id": "alias_name_10",
                },
                # dataset alias (not resolved) as outlets (target)
                {
                    "source": "dag2",
                    "target": "dataset-alias",
                    "dependency_type": "dataset-alias",
                    "dependency_id": "alias_name_2",
                },
            ],
            "params": [],
        },
    }
    expected_dag_dependencies = [
        # asset as schedule (source)
        {
            "dependency_id": "dataset_uri_1",
            "dependency_type": "asset",
            "label": "dataset_uri_1",
            "source": "asset",
            "target": "dag1",
        },
        # asset alias (resolved) as schedule (source)
        {
            "dependency_id": "dataset_uri_2",
            "dependency_type": "asset",
            "label": "dataset_uri_2",
            "source": "asset",
            "target": "asset-alias:alias_name_1",
        },
        {
            "dependency_id": "alias_name_1",
            "dependency_type": "asset-alias",
            "label": "alias_name_1",
            "source": "asset:alias_name_1",
            "target": "dag2",
        },
        # asset alias (not resolved) as schedule (source)
        {
            "dependency_id": "alias_name_2",
            "dependency_type": "asset-alias",
            "label": "alias_name_2",
            "source": "asset-alias",
            "target": "dag2",
        },
        # asset as outlets (target)
        {
            "dependency_id": "dataset_uri_10",
            "dependency_type": "asset",
            "label": "dataset_uri_10",
            "source": "dag10",
            "target": "asset",
        },
        # asset alias (resolved) as outlets (target)
        {
            "dependency_id": "dataset_uri_20",
            "dependency_type": "asset",
            "label": "dataset_uri_20",
            "source": "dag20",
            "target": "asset-alias:alias_name_10",
        },
        {
            "dependency_id": "alias_name_10",
            "dependency_type": "asset-alias",
            "label": "alias_name_10",
            "source": "asset:dataset_uri_20",
            "target": "asset-alias",
        },
        # asset alias (not resolved) as outlets (target)
        {
            "dependency_id": "alias_name_2",
            "dependency_type": "asset-alias",
            "label": "alias_name_2",
            "source": "dag2",
            "target": "asset-alias",
        },
    ]

    SerializedDAG.conversion_v1_to_v2(v1)

    # Update a few subtle differences
    v1["dag"]["tags"] = []
    v1["dag"]["catchup"] = False
    v1["dag"]["disable_bundle_versioning"] = False

    expected = copy.deepcopy(serialized_simple_dag_ground_truth)
    expected["dag"]["dag_dependencies"] = expected_dag_dependencies
    del expected["dag"]["tasks"][1]["__var"]["_operator_extra_links"]

    assert v1 == expected
