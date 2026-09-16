"""Validation of native receipts in retained captures, not a history API."""
from pydantic import RootModel
from services.bioxp.operator_models import OperatorRecordedReceipt


class RecordedReceipts(RootModel[list[OperatorRecordedReceipt]]):
    pass
