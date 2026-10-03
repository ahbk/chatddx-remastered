GRANT SELECT, INSERT, UPDATE ON identity.credential TO chatddx_writer;
GRANT SELECT, INSERT, DELETE ON identity.session TO chatddx_writer;
-- chatddx_reader gets neither: they hold password hashes and session tokens.
