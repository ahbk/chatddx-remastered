REVOKE ALL ON ALL TABLES IN SCHEMA ledger FROM PUBLIC;
REVOKE ALL ON SCHEMA ledger FROM PUBLIC;

-- chatddx_reader gets nothing: run_item and score_item hold case-derived records
-- (src/chatddx/ledger/record.py: Record.case_derived).
GRANT USAGE ON SCHEMA ledger TO chatddx_writer;
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA ledger TO chatddx_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA ledger
    GRANT SELECT, INSERT ON TABLES TO chatddx_writer;
