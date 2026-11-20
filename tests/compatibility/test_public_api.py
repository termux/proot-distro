import dataclasses
import inspect
import json
from pathlib import Path
import types
import typing

import proot_distro.api as api


_DATACLASSES = (
    "BackupResult",
    "BuildResult",
    "CacheCleanupResult",
    "ContainerInfo",
    "CredentialRequest",
    "DetachedSession",
    "ImageInfo",
    "ImageRemovalResult",
    "OperationEvent",
    "PushResult",
    "RegistryCredentials",
    "RestoreResult",
    "RuntimeConfig",
    "SearchHit",
    "SearchResult",
    "SessionCommand",
    "SessionInfo",
    "SessionSignalResult",
    "TransferResult",
)

_ERRORS = (
    "AlreadyExistsError",
    "AuthenticationError",
    "BusyError",
    "IntegrityError",
    "InvalidArgumentError",
    "NetworkError",
    "NotFoundError",
    "OperationCancelledError",
    "OperationError",
    "PartialOperationError",
    "ProotDistroError",
    "SessionStartupTimeoutError",
    "UnsupportedOperationError",
)

_PROTOCOL_MEMBERS = {
    "CancellationToken": ("is_set",),
    "CredentialProvider": ("__call__",),
    "EventHandler": ("__call__",),
}

_SNAPSHOT = Path(__file__).with_name("api_v1.json")
_UNION_ORIGINS = (typing.Union,)
if hasattr(types, "UnionType"):
    _UNION_ORIGINS += (types.UnionType,)


def _annotation(value):
    if value is inspect.Parameter.empty or value is inspect.Signature.empty:
        return "<missing>"
    if value is type(None):
        return "None"
    if value is typing.Any:
        return "Any"
    if value is Ellipsis:
        return "..."
    origin = typing.get_origin(value)
    arguments = typing.get_args(value)
    if origin in _UNION_ORIGINS:
        if len(arguments) == 2 and type(None) in arguments:
            selected = (
                arguments[0] if arguments[1] is type(None) else arguments[1]
            )
            return "Optional[" + _annotation(selected) + "]"
        return "Union[" + ", ".join(
            _annotation(argument) for argument in arguments
        ) + "]"
    if origin is not None:
        origin_names = {
            dict: "Dict",
            frozenset: "FrozenSet",
            list: "List",
            set: "Set",
            tuple: "Tuple",
        }
        name = origin_names.get(origin)
        if name is None:
            name = getattr(origin, "__name__", None)
        if name is None:
            name = inspect.formatannotation(origin).replace("typing.", "")
        if getattr(origin, "__module__", "") in ("os", "signal", "subprocess"):
            name = origin.__module__ + "." + name
        return name + "[" + ", ".join(
            _annotation(argument) for argument in arguments
        ) + "]"
    module = getattr(value, "__module__", "")
    name = getattr(value, "__qualname__", None)
    if name is not None:
        if module in ("os", "signal", "subprocess"):
            return module + "." + name
        return name
    return inspect.formatannotation(value).replace("typing.", "")


def _signature(target, *, hints_target=None, drop_first=False):
    signature = inspect.signature(target)
    hints = typing.get_type_hints(hints_target or target)
    parameters = []
    raw_parameters = list(signature.parameters.values())
    for index, parameter in enumerate(raw_parameters):
        if drop_first and index == 0 and parameter.name in ("self", "cls"):
            continue
        if (parameter.kind is inspect.Parameter.KEYWORD_ONLY
                and not any(item == "*" or item.startswith("*")
                            for item in parameters)):
            parameters.append("*")
        prefix = ""
        if parameter.kind is inspect.Parameter.VAR_POSITIONAL:
            prefix = "*"
        elif parameter.kind is inspect.Parameter.VAR_KEYWORD:
            prefix = "**"
        text = prefix + parameter.name
        annotation = hints.get(parameter.name, parameter.annotation)
        if annotation is not inspect.Parameter.empty:
            text += ": " + _annotation(annotation)
        if parameter.default is not inspect.Parameter.empty:
            text += " = " + repr(parameter.default)
        parameters.append(text)
        if (parameter.kind is inspect.Parameter.POSITIONAL_ONLY
                and (index + 1 == len(raw_parameters)
                     or raw_parameters[index + 1].kind
                     is not inspect.Parameter.POSITIONAL_ONLY)):
            parameters.append("/")
    returns = hints.get("return", signature.return_annotation)
    return "(" + ", ".join(parameters) + ") -> " + _annotation(returns)


def _members(cls, *, include=()):
    result = {}
    for name, raw in vars(cls).items():
        if name.startswith("_") and name not in include:
            continue
        value = getattr(cls, name)
        if isinstance(raw, property):
            result[name] = {
                "kind": "property",
                "signature": _signature(raw.fget, drop_first=True),
            }
        elif callable(value):
            result[name] = {
                "kind": "method",
                "signature": _signature(value),
            }
    return result


def _dataclass_contract(cls):
    hints = typing.get_type_hints(cls)
    fields = []
    for field in dataclasses.fields(cls):
        if field.default is dataclasses.MISSING:
            default = "<required>"
        else:
            default = repr(field.default)
        fields.append({
            "name": field.name,
            "annotation": _annotation(hints[field.name]),
            "default": default,
            "repr": field.repr,
        })
    return {
        "constructor": _signature(cls, hints_target=cls.__init__),
        "frozen": cls.__dataclass_params__.frozen,
        "init": cls.__dataclass_params__.init,
        "fields": fields,
        "members": _members(cls),
    }


def _public_api_snapshot():
    dataclass_contracts = {
        name: _dataclass_contract(getattr(api, name))
        for name in _DATACLASSES
    }
    errors = {}
    for name in _ERRORS:
        cls = getattr(api, name)
        instance = cls("snapshot", operation="compatibility")
        errors[name] = {
            "bases": [base.__name__ for base in cls.__bases__],
            "operation": instance.operation,
        }
    protocols = {}
    for name, members in _PROTOCOL_MEMBERS.items():
        cls = getattr(api, name)
        protocols[name] = {
            "runtime_checkable": bool(
                getattr(cls, "_is_runtime_protocol", False)
            ),
            "members": {
                member: _signature(getattr(cls, member), drop_first=True)
                for member in members
            },
        }
    classified = {
        "API_VERSION",
        "EventKind",
        "ProotDistro",
        "SessionProcess",
        *_DATACLASSES,
        *_ERRORS,
        *_PROTOCOL_MEMBERS,
    }
    assert set(api.__all__) == classified
    return {
        "api_version": api.API_VERSION,
        "exports": list(api.__all__),
        "classes": {
            "ProotDistro": {
                "constructor": _signature(
                    api.ProotDistro,
                    hints_target=api.ProotDistro.__init__,
                ),
                "members": _members(api.ProotDistro),
            },
            "SessionProcess": {
                "attributes": {
                    name: _annotation(value)
                    for name, value in typing.get_type_hints(
                        api.SessionProcess
                    ).items()
                    if not name.startswith("_")
                },
                "members": _members(
                    api.SessionProcess,
                    include=("__enter__", "__exit__"),
                ),
            },
        },
        "dataclasses": dataclass_contracts,
        "enum": {
            "EventKind": [
                {"name": member.name, "value": member.value}
                for member in api.EventKind
            ],
        },
        "errors": errors,
        "protocols": protocols,
    }


def test_public_api_matches_versioned_compatibility_snapshot():
    expected = json.loads(_SNAPSHOT.read_text(encoding="utf-8"))
    assert _public_api_snapshot() == expected


def test_public_callable_annotations_are_complete():
    def assert_complete(target, *, hints_target=None):
        signature = inspect.signature(target)
        hints = typing.get_type_hints(hints_target or target)
        assert (
            hints.get("return", signature.return_annotation)
            is not inspect.Signature.empty
        )
        for parameter in signature.parameters.values():
            if parameter.name in ("self", "cls"):
                continue
            assert (
                hints.get(parameter.name, parameter.annotation)
                is not inspect.Parameter.empty
            )

    assert_complete(
        api.ProotDistro, hints_target=api.ProotDistro.__init__
    )
    for cls in (api.ProotDistro, api.SessionProcess):
        for name, raw in vars(cls).items():
            if (name.startswith("_")
                    and not (cls is api.SessionProcess
                             and name in ("__enter__", "__exit__"))):
                continue
            if isinstance(raw, property):
                assert_complete(raw.fget)
            elif callable(getattr(cls, name)):
                assert_complete(getattr(cls, name))
    for name in _DATACLASSES:
        cls = getattr(api, name)
        assert_complete(cls, hints_target=cls.__init__)
        for member_name, raw in vars(cls).items():
            if member_name.startswith("_"):
                continue
            if isinstance(raw, property):
                assert_complete(raw.fget)
            elif callable(getattr(cls, member_name)):
                assert_complete(getattr(cls, member_name))
    for name, members in _PROTOCOL_MEMBERS.items():
        cls = getattr(api, name)
        for member in members:
            assert_complete(getattr(cls, member))
