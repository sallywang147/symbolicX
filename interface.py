import json
import os
import tempfile
import yaml
import argparse
from typing import Dict, Final, List, Optional, Tuple, Union

from maat import Cst, EVMTransaction, Value, Var, VarContext

from common.abi import function_call
from common.exceptions import EchidnaException, GenericException
from common.logger import logger
from common.util import (
    twos_complement_convert,
    int_to_bool,
    echidna_parse_bytes,
    echidna_encode_bytes,
)
from common.world import AbstractTx

# Prefix for files containing new inputs generated by the symbolic executor
NEW_INPUT_PREFIX: Final[str] = "optik_solved_input"


def translate_argument_type(arg: Dict) -> str:
    """Translates a serialized argument's type into a string"""
    t = arg["tag"]
    if t == "AbiUInt":
        bits = arg["contents"][0]
        return f"uint{bits}"

    if t == "AbiUIntType":
        bits = arg["contents"]
        return f"uint{bits}"

    if t == "AbiInt":
        bits = arg["contents"][0]
        return f"int{bits}"

    if t == "AbiIntType":
        bits = arg["contents"]
        return f"int{bits}"

    if t.startswith("AbiAddress"):
        return "address"

    if t == "AbiBytes":
        byteLen = arg["contents"][0]
        return f"bytes{byteLen}"

    if t == "AbiBytesType":
        byteLen = arg["contents"]
        return f"bytes{byteLen}"

    if t.startswith("AbiString"):
        return "string"

    if t.startswith("AbiBytesDynamic"):
        return f"bytes"

    if t.startswith("AbiBool"):
        return "bool"

    if t == "AbiArrayDynamic":
        el_type = translate_argument_type(arg["contents"][0])
        return f"{el_type}[]"

    if t == "AbiArrayDynamicType":
        el_type = translate_argument_type(arg["contents"])
        return f"{el_type}[]"

    if t.startswith("AbiArray"):
        num_elems = arg["contents"][0]
        el_type = translate_argument_type(arg["contents"][1])
        return f"{el_type}[{num_elems}]"

    if t.startswith("AbiTuple"):
        contents = arg["contents"]
        types = [translate_argument_type(el) for el in contents]
        return f"({','.join(types)})"

    raise EchidnaException(f"Unsupported argument type: {t}")


def translate_argument_value(arg: Dict) -> Union[bytes, int, Value]:
    """Translate a serialized argument into a python value"""
    t = arg["tag"]
    if t == "AbiUInt":
        return int(arg["contents"][1])

    if t == "AbiInt":
        return int(arg["contents"][1])

    if t == "AbiAddress":
        return int(arg["contents"], 16)

    if t == "AbiBytes":
        return echidna_parse_bytes(arg["contents"][1])

    if t == "AbiString":
        return echidna_parse_bytes(arg["contents"])

    if t == "AbiBool":
        return arg["contents"]

    if t == "AbiBytesDynamic":
        return echidna_parse_bytes(arg["contents"])

    if t == "AbiArray":
        array = arg["contents"][2]
        return [translate_argument_value(el) for el in array]

    if t == "AbiArrayDynamic":
        array = arg["contents"][1]
        return [translate_argument_value(el) for el in array]

    if t == "AbiTuple":
        contents = arg["contents"]
        return [translate_argument_value(el) for el in contents]

    raise EchidnaException(f"Unsupported argument type: {t}")


def translate_argument(arg: Dict) -> Tuple[str, Union[bytes, int, Value]]:
    """Translate a parsed Echidna transaction argument into a '(type, value)' tuple.
    :param arg: Transaction argument parsed as a json dict
    """

    return (
        translate_argument_type(arg),
        translate_argument_value(arg),
    )


def extract_func_from_call(call: Dict) -> Tuple[str, str, List]:
    """Extract function name, argument spec, and values, from a JSON
    serialized Echidna transaction

    :param call: Call from Echidna transaction
    """
    if call["tag"] != "SolCall":
        raise EchidnaException(f"Unsupported transaction tag: '{call['tag']}'")

    arg_types = []
    arg_values = []
    func_name: str = call["contents"][0]
    if len(call["contents"]) > 1:
        for arg in call["contents"][1]:
            t, val = translate_argument(arg)
            arg_types.append(t)
            arg_values.append(val)

    args_spec = f"({','.join(arg_types)})"

    return func_name, args_spec, arg_values


def load_tx(tx: Dict, tx_name: str = "") -> AbstractTx:
    """Translates a parsed echidna transaction into a Maat transaction

    :param tx: Echidna transaction parsed as a json dict
    :param tx_name: Optional name identifying this transaction, used to
        name symbolic variables created to fill the transaction data
    """

    ctx = VarContext()

    # Translate block number/timestamp increments
    block_num_inc = Var(256, f"{tx_name}_block_num_inc")
    block_timestamp_inc = Var(256, f"{tx_name}_block_timestamp_inc")
    ctx.set(block_num_inc.name, int(tx["_delay"][1], 16), block_num_inc.size)
    ctx.set(
        block_timestamp_inc.name, int(tx["_delay"][0], 16), block_num_inc.size
    )

    # Check if it's a "NoCall" echidna transaction
    if tx["_call"]["tag"] == "NoCall":
        return AbstractTx(
            None,
            block_num_inc,
            block_timestamp_inc,
            ctx,
        )

    # Translate function call
    func_name, args_spec, arg_values = extract_func_from_call(tx["_call"])
    call_data = function_call(func_name, args_spec, ctx, tx_name, *arg_values)

    # Translate message sender
    sender = Var(160, f"{tx_name}_sender")
    ctx.set(sender.name, int(tx["_src"], 16), sender.size)

    # Translate message value
    # Echidna will only send non-zero msg.value to payable funcs
    # so we only make an abstract value in that case
    if int(tx["_value"], 16) != 0:
        value = Var(256, f"{tx_name}_value")
        ctx.set(value.name, int(tx["_value"], 16), value.size)
    else:
        value = Cst(256, 0)

    # Build transaction
    # TODO: make EVMTransaction accept integers as arguments
    gas_limit = Cst(256, int(tx["_gas'"], 16))
    gas_price = Cst(256, int(tx["_gasprice'"], 16))
    recipient = int(tx["_dst"], 16)
    return AbstractTx(
        EVMTransaction(
            sender,  # origin
            sender,  # sender
            recipient,  # recipient
            value,  # value
            call_data,  # data
            gas_price,  # gas price
            gas_limit,  # gas_limit
        ),
        block_num_inc,
        block_timestamp_inc,
        ctx,
    )


def load_tx_sequence(filename: str) -> List[AbstractTx]:
    """Load a sequence of transactions from an Echidna corpus file

    :param filename: corpus file to load
    """
    with open(filename, "rb") as f:
        data = json.loads(f.read())
        res = []
        for i, tx in enumerate(data):
            res.append(load_tx(tx, tx_name=f"tx{i}"))
        return res


def update_argument(arg: Dict, arg_name: str, new_model: VarContext) -> None:
    """Update an argument value in a transaction according to a
    symbolic model. The argument is modified **in-place**

    :param arg: argument to update, parsed as a JSON dict
    :param arg_name: base name of the symbolic variable that was created for this
    argument
    :param new_model: symbolic model to use to update the argument value
    """

    def is_array_like_type(arg_type: str) -> bool:
        """Return True if encoding the type results in multiple variables,
        i.e arg_0, arg_1, ... arg_N"""
        return any(
            [x for x in ["Tuple", "Array", "Bytes", "String"] if x in arg_type]
        )

    def _update_bytes_like_argument(
        arg_name: str,
        length: int,
        val: List,
        new_model: VarContext,
    ) -> None:
        """Update bytes-like object (bytes, string, dynamic bytes, ...)"""
        for i in range(length):
            byte_name = f"{arg_name}_{i}"
            if new_model.contains(byte_name):
                val[i] = new_model.get(byte_name) & 0xFF

    argType = arg["tag"]

    # Update the argument only if the model contains a new value
    # for this argument
    if is_array_like_type(argType) and all(
        [arg_name not in var for var in new_model.contained_vars()]
    ):
        return
    if not is_array_like_type(argType) and not new_model.contains(arg_name):
        return

    if argType == "AbiUInt":
        arg["contents"][1] = str(new_model.get(arg_name))

    elif argType == "AbiInt":
        argVal = int(new_model.get(arg_name))
        bits = arg["contents"][0]
        arg["contents"][1] = str(twos_complement_convert(argVal, bits))

    elif argType == "AbiBool":
        argVal = new_model.get(arg_name)
        arg["contents"] = int_to_bool(argVal)

    elif argType == "AbiAddress":
        arg["contents"] = str(hex(new_model.get(arg_name)))

    elif argType == "AbiBytes":
        length = arg["contents"][0]
        val = echidna_parse_bytes(arg["contents"][1])
        _update_bytes_like_argument(arg_name, length, val, new_model)
        arg["contents"][1] = echidna_encode_bytes(val)

    elif argType in ["AbiBytesDynamic", "AbiString"]:
        val = echidna_parse_bytes(arg["contents"])
        _update_bytes_like_argument(arg_name, len(val), val, new_model)
        arg["contents"] = echidna_encode_bytes(val)

    elif argType == "AbiTuple":
        tuple_els = arg["contents"]
        for i, el in enumerate(tuple_els):
            sub_arg_name = f"{arg_name}_{i}"
            update_argument(el, sub_arg_name, new_model)

    elif argType == "AbiArray":
        arr_els = arg["contents"][2]
        for i, el in enumerate(arr_els):
            sub_arg_name = f"{arg_name}_{i}"
            update_argument(el, sub_arg_name, new_model)

    elif argType == "AbiArrayDynamic":
        arr_els = arg["contents"][1]
        for i, el in enumerate(arr_els):
            sub_arg_name = f"{arg_name}_{i}"
            update_argument(el, sub_arg_name, new_model)

    else:
        raise EchidnaException(f"Unsupported argument type: {argType}")


def update_tx(tx: Dict, new_model: VarContext, tx_name: str = "") -> Dict:
    """Update parameter values in a transaction according to a
    symbolic model

    :param tx: Echidna transaction to update, parsed as a JSON dict
    :param new_model: symbolic model to use to update transaction data in 'tx'
    :param tx_name: Optional name identifying the transaction to update, used to
        name get symbolci variables corresponding to the transaction data. Needs
        to match the 'tx_name' passed to load_tx() earlier
    :return: the updated transaction as a JSON dict
    """
    tx = tx.copy()  # Copy transaction to avoid in-place modifications

    # Update call arguments
    call = tx["_call"]
    args = call["contents"][1]
    for i, arg in enumerate(args):
        update_argument(arg, f"{tx_name}_arg{i}", new_model)

    # Update block number & timestamp
    block_num_inc = f"{tx_name}_block_num_inc"
    block_timestamp_inc = f"{tx_name}_block_timestamp_inc"
    if new_model.contains(block_num_inc):
        tx["_delay"][1] = hex(new_model.get(block_num_inc))
    if new_model.contains(block_timestamp_inc):
        tx["_delay"][0] = hex(new_model.get(block_timestamp_inc))

    # Update sender
    sender = f"{tx_name}_sender"
    if new_model.contains(sender):
        # Address so we need to pad it to 40 chars (20bytes)
        tx["_src"] = f"0x{new_model.get(sender):0{40}x}"

    # Update transaction value
    value = f"{tx_name}_value"
    if new_model.contains(value):
        tx["_value"] = hex(new_model.get(value))

    return tx


def store_new_tx_sequence(original_file: str, new_model: VarContext) -> None:
    """Store a new sequence of transactions into a new Echidna corpus file

    :param original_file: path to the file containing the original transaction sequence
    that was replayed in order to find the new input
    :param new_model: symbolic context containing new values for the transaction data
    """
    # Load original JSON corpus input
    with open(original_file, "rb") as f:
        data = json.loads(f.read())

    # Update JSON with new transactions
    new_data = []
    for i, tx in enumerate(data):
        new_data.append(update_tx(tx, new_model, tx_name=f"tx{i}"))

    # Write new corpus input in a fresh file
    new_file = get_available_filename(
        f"{os.path.dirname(original_file)}/{NEW_INPUT_PREFIX}", ".txt"
    )
    with open(new_file, "w") as f:
        json.dump(new_data, f)


def get_available_filename(prefix: str, suffix: str) -> str:
    """Get an avaialble filename. The filename will have the
    form '<prefix>_<num><suffix>' where <num> is automatically
    generated based on existing files

    :param prefix: the new file prefix, including potential absolute the path
    :param suffix: the new file suffix
    """
    num = 0
    num_max = 100000
    while os.path.exists(f"{prefix}_{num}{suffix}") and num < num_max:
        num += 1
    if num >= num_max:
        raise GenericException("Can't find available filename, very odd")
    return f"{prefix}_{num}{suffix}"


def extract_contract_bytecode(
    crytic_dir: str, contract_name: Optional[str]
) -> Optional[str]:
    """Parse compilation information from crytic, extracts the bytecodes
    of compiled contracts, and stores them into separate files.

    :param crytic-dir: the "crytic-export" dir created by echidna after a campaign
    :param contract_name: the name of the contract to extract
    :return: path to a file containing the bytecode for 'contract', or None on failure
    """

    def _name_from_path(path):
        return path.split(":")[-1]

    solc_file = str(os.path.join(crytic_dir, "combined_solc.json"))
    with open(solc_file, "rb") as f:
        data = json.loads(f.read())
        all_contracts = data["contracts"]
        all_contract_names = ",".join(iter(all_contracts))
        if contract_name is None:
            if len(all_contracts) == 1:
                contract_name = _name_from_path(next(iter(all_contracts)))
            else:
                logger.error(
                    f"Please specify the target contract among: {all_contract_names}"
                )
                return None

        for contract_path, contract_data in data["contracts"].items():
            if contract_name == _name_from_path(contract_path):
                bytecode = contract_data["bin"]
                output_file: str = tempfile.NamedTemporaryFile(
                    prefix="optik_contract_",
                    suffix=".sol",
                    delete=False,
                ).name
                with open(output_file, "w") as f2:
                    logger.debug(
                        f"Bytecode for contract {contract_name} written in {output_file}"
                    )
                    f2.write(bytecode)
                return output_file

        # Didn't find contract
        logger.fatal(
            f"Couldn't find bytecode for contract {contract_name} in {solc_file}. "
            f"Available contracts: {all_contract_names}"
        )
        return None


def extract_cases_from_json_output(output: str) -> List[List[str]]:
    """Extract echidna test cases from it's JSON output (obtained by
    passing '--format json'). Returns a list of test cases. Each test
    case in the list consists in the list of function calls that
    make up this case.
    Example return value:
    [
        ["f()", "g(1,2)"],
        ["h(12343, -1)"]
    ]

    :param output: echidna's JSON output as a single string
    """
    # Sometimes the JSON output starts with a line such as
    # "Loaded total of 500 transactions from /tmp/c4/coverage"
    if output.startswith("Loaded total of"):
        output = output.split("\n", 1)[1]
    data = json.loads(output)
    if "tests" not in data:
        return []
    res = []
    for test in data["tests"]:
        if test["status"] == "solved":
            case = []
            for tx in test["transactions"]:
                case.append(
                    f"{tx['function']}({','.join([arg for arg in tx['arguments']])})"
                )
            res.append(case)
    return res


def count_cov_lines(coverage_file: str) -> int:
    """Count the number of lines covered from an Echidna coverage file.

    :param coverage_file: an Echidna covered.*.txt type of file, with "*"
    characters at the beginning of code lines that was covered during
    fuzzing"""
    with open(coverage_file, "r") as f:
        return len([1 for line in f.readlines() if line[0] in "*e"])


def get_latest_coverage_file(
    corpus_dir: str,
) -> Optional[str]:
    """Returns the path to covered.<timestamp>.txt file generated by echidna
    in the 'corpus_dir' directory. Returns None if no such file exists"""
    # Get the first file after reverse sorting the filename list, so
    # that we get the latest coverage file (name with the bigger timestamp)
    try:
        for filename in sorted(os.listdir(corpus_dir), reverse=True):
            if filename.startswith("covered.") and filename.endswith(".txt"):
                return os.path.join(corpus_dir, filename)
    except FileNotFoundError:
        pass
    return None


def count_unique_pc(output: str) -> int:
    """Count the unique instruction addresses that were executed
    by echidna

    :param output: echidna's JSON output as a simple string
    """
    if output.startswith("Loaded total of"):
        output = output.split("\n", 1)[1]
    data = json.loads(output)
    res = 0
    for addr, cov in data["coverage"].items():
        unique_pcs = set([x[0] for x in cov])
        res += len(unique_pcs)
    return res


def get_echidna_init_file(args: argparse.Namespace) -> Optional[str]:
    """Return the echidna init file name, or None if no file or config
    was specified"""
    if args.config is None:
        return None
    with open(args.config, "r") as f:
        try:
            config = yaml.safe_load(f)
        except Exception as e:
            raise EchidnaException(
                f"Failed to parse config file {args.config}"
            ) from e
        return config.get("initialize", None)
