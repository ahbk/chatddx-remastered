-- The column holds the vignette's fingerprint; the vignette itself is (source, source_id, fingerprint).
ALTER TABLE catalog.binding RENAME COLUMN vignette TO fingerprint;
