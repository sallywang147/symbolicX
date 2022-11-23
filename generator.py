import json
import os
from typing import List, Dict, Final, Optional

from slither.slither import SlitherCore

from common.abi import func_signature
from common.exceptions import CorpusException
from common.logger import logger
from dataflow.dataflow import (
    DataflowGraph,
    DataflowNode,
    get_base_dataflow_graph,
)


# Prefix for files containing seed corpus
SEED_CORPUS_PREFIX: Final[str] = "optik_corpus"

# TODO: remove?
# import itertools
# def all_combinations(choices: Set[Any]) -> List[List[Any]]:
#     res = [
#         list(x)
#         for r in range(1, len(choices) + 1)
#         for x in itertools.combinations(choices, r)
#     ]
#     return res


class CorpusGenerator:
    """Abstract class for fuzzing corpus generation based on dataflow analysis
    with slither"""

    def __init__(self, contract_name: str, slither: SlitherCore):
        self.dataflow_graph: DataflowGraph = get_base_dataflow_graph(
            contract_name, slither
        )
        self.func_template_mapping: Dict[str, DataflowNode] = {}
        self.current_tx_sequences: List[List[DataflowNode]] = []
        # Initialize basic set of 1-tx sequences
        self._init()

    def _init(self) -> None:
        """Create initial set of sequences of 1 transaction each"""
        self.current_tx_sequences = [[n] for n in self.dataflow_graph.nodes]

    @property
    def current_seq_len(self) -> int:
        return (
            0
            if not self.current_tx_sequences
            else len(self.current_tx_sequences[0])
        )

    def _step(self) -> None:
        """Update the current transaction sequences 1 time. See step() for
        more details"""
        new_tx_sequences = []
        for tx_seq in self.current_tx_sequences:
            # Get all txs that can impact this sequence
            impacts_seq = set().union(*[n.parents for n in tx_seq])
            # Prepend impacting tx(s) to sequence
            new_tx_sequences += [[prev] + tx_seq for prev in impacts_seq]
        self.current_tx_sequences = new_tx_sequences

    def step(self, n: int = 1) -> None:
        """Update the current transaction sequences 'n' times.
        Updating the current transaction sequences if done by prepending one call
        to all sequences. If multiple calls impact a sequence, it create as many
        new sequences as there are such calls.
        """
        for _ in range(n):
            self._step()

    def dump_tx_sequences(self, corpus_dir: str) -> None:
        """Dump the current dataflow tx sequences in new corpus input files"""
        raise NotImplementedError()

    def __str__(self) -> str:
        res = f"Dataflow graph:\n {self.dataflow_graph}\n"

        res += "Current tx sequences:\n"
        for i, tx_seq in enumerate(self.current_tx_sequences):
            res += (
                f"{i}: "
                + " -> ".join([node.func.name for node in tx_seq])
                + "\n"
            )

        return res
