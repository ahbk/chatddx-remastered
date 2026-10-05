-- src/chatddx/catalog/threads.py: variation takes a fork's latest based_on, else its forked_from, as its base.
ALTER TABLE catalog.edit
    ADD COLUMN based_on bigint,
    ADD FOREIGN KEY (based_on, kind) REFERENCES catalog.edit (id, kind);
