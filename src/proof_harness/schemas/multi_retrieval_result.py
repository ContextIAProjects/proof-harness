"""MultiRetrievalResult: one deterministic search over N banks.

Composition, not amendment (D34): each bank embeds a verbatim
``RetrievalResult`` — revalidated against ITS OWN anchor, carrying its own
pinned pair. Bank order is the banks-file order and is part of the
canonical document. Labels are unique; the wrapper carries no volatile
fields, so the whole document reproduces byte-identical for frozen banks
and indexes.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from proof_harness.schemas.common import ArtifactModel, StrictModel
from proof_harness.schemas.retrieval_result import RetrievalResult, SearchQuery

BANK_LABEL_PATTERN = r"^[a-z][a-z0-9-]*$"


class BankResult(StrictModel):
    label: str = Field(pattern=BANK_LABEL_PATTERN)
    result: RetrievalResult


class MultiRetrievalResult(ArtifactModel):
    query: SearchQuery
    banks: list[BankResult] = Field(min_length=1)

    @model_validator(mode="after")
    def _labels_are_unique(self) -> MultiRetrievalResult:
        labels = [bank.label for bank in self.banks]
        if len(labels) != len(set(labels)):
            raise ValueError(f"bank labels must be unique: {labels}")
        return self
