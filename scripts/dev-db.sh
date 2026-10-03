CHATDDX_PGDATA="$REPO_ROOT/dev-db/pgdata"
CHATDDX_PGSOCK="$REPO_ROOT/dev-db/pgsock"
osuser="$(id -un)"

usage() {
  echo "Usage: dev-db {start|stop|enter [psql args...]}"
}

init_pg() {
  initdb -D "$CHATDDX_PGDATA" -U "$osuser" --auth-local=peer --auth-host=reject --encoding=UTF8 >/dev/null

  cat >"$CHATDDX_PGDATA/pg_ident.conf" <<EOF
# MAPNAME  SYSTEM-USERNAME  PG-USERNAME
chatddx    $osuser          chatddx
chatddx    $osuser          chatddx_writer
chatddx    $osuser          $osuser
EOF

  echo "local all all peer map=chatddx" >"$CHATDDX_PGDATA/pg_hba.conf"
}

start_pg() {
  mkdir -p "$CHATDDX_PGSOCK"
  pg_ctl \
    -D "$CHATDDX_PGDATA" \
    -l "$CHATDDX_PGDATA/server.log" \
    -w start \
    -o "-c listen_addresses='' -c unix_socket_directories='$CHATDDX_PGSOCK'"
}

report_pg_failure() {
  echo "postgres failed to start; last log lines:" >&2
  tail -n 20 "$CHATDDX_PGDATA/server.log" >&2
  exit 1
}

pg_is_running() {
  pg_ctl -D "$CHATDDX_PGDATA" status >/dev/null 2>&1
}

setup_db() {
  if ! psql -h "$CHATDDX_PGSOCK" -U "$osuser" -d postgres -q \
    -v ON_ERROR_STOP=1 -f "$REPO_ROOT/src/chatddx/store/setup.sql"; then
    echo "setup failed; reset with: dev-db stop && rm -rf $CHATDDX_PGDATA" >&2
    exit 1
  fi
  touch "$CHATDDX_PGDATA/.setup-done"
}

start_db() {
  [ -d "$CHATDDX_PGDATA" ] || init_pg
  if pg_is_running; then
    echo "postgres is already running"
  else
    start_pg || report_pg_failure
  fi
  [ -e "$CHATDDX_PGDATA/.setup-done" ] || setup_db
}

stop_db() {
  if pg_is_running; then
    pg_ctl -D "$CHATDDX_PGDATA" stop
  else
    echo "postgres not running"
  fi
}

enter_db() {
  PGHOST="$CHATDDX_PGSOCK" PGUSER=chatddx PGDATABASE=chatddx exec psql "$@"
}

reset_db() {
  stop_db
  rm -rf "$CHATDDX_PGDATA" "$CHATDDX_PGSOCK"
}

case "${1:-}" in
start) start_db ;;
stop) stop_db ;;
status) pg_is_running && echo "running" || echo "stopped" ;;
enter)
  shift
  enter_db "$@"
  ;;
reset) reset_db ;;
*)
  usage
  exit 1
  ;;
esac
