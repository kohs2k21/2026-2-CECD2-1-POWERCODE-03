"""Use the same frozen family-partitioned runner for approved S/F metadata."""
import replay_esb_export_observation as replay


if __name__ == "__main__":
    replay.TABLES = {
        "TransactionStatusExport": ("EC_TRANSACTION_MONITORING.csv",
                                    ("TRANSACTION_ID", "STATUS", "RETRY_COUNT")),
        "ProcessStatusExport": ("EC_PROCESS_MONITORING.csv",
                                ("TRANSACTION_ID", "PROCESS_ID", "STATUS", "RETRY_COUNT")),
    }
    replay.main()
