#!/usr/bin/env python3
"""
smart-dump-mysql-skill 的确定性执行脚本。

依赖: 仅 Python 3 标准库, 系统需安装 mysql-client (提供 mysql 与 mysqldump 命令)。
密码: 优先通过环境变量 MYSQL_PWD 传入, 避免出现在命令行参数 / 进程列表中。

子命令:
  verify          校验登录并列出可访问的数据库
  parse-jdbc      解析 JDBC 连接地址 (jdbc:mysql://host:port/db?params)
  parse-mysql-cmd 解析 mysql 命令行 (mysql -h host -P port -u user -p db)
  list-tables     列出数据库中的全部表 (含类型: BASE TABLE / VIEW)
  count           查询单表行数
  dump-schema     导出 create-db.sql 与 schema.sql
  dump-table      按行数阈值导出单表数据或创建占位文件
  dump-all        依次执行 dump-schema 与全部表的 dump-table
"""

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

CHARSET = "utf8mb4"
DEFAULT_PORT = 3306
DEFAULT_MAX_ROWS = 3000
MYSQL_PWD_ENV = "MYSQL_PWD"


class DumpError(Exception):
    pass


def quote_ident(name):
    """MySQL 标识符转义, 返回反引号包裹的名称。"""
    return "`" + str(name).replace("`", "``") + "`"


def safe_component(name):
    """用于输出目录 / 文件名, 防止路径穿越; 保留中英文等可读字符。"""
    cleaned = str(name).replace("/", "_").replace("\\", "_").replace("\x00", "_")
    cleaned = re.sub(r"^\.+", "", cleaned)
    return cleaned or "unnamed"


def build_env(password):
    env = os.environ.copy()
    if password is not None:
        env[MYSQL_PWD_ENV] = password
    else:
        env.pop(MYSQL_PWD_ENV, None)
    return env


def run(cmd, password, check=True):
    proc = subprocess.run(
        cmd,
        env=build_env(password),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and proc.returncode != 0:
        raise DumpError(
            f"命令执行失败: {' '.join(cmd[:2])} ... (exit {proc.returncode})\n"
            f"{proc.stderr.strip()}"
        )
    return proc


def mysql_cmd(conn, extra=None):
    cmd = [
        "mysql",
        f"--default-character-set={CHARSET}",
        "--connect-timeout=10",
        "-N",
        "-B",
        "-h",
        conn.host,
        "-P",
        str(conn.port),
        "-u",
        conn.user,
    ]
    if extra:
        cmd += extra
    return cmd


def mysqldump_cmd(conn, extra=None):
    cmd = [
        "mysqldump",
        f"--default-character-set={CHARSET}",
        "--connect-timeout=10",
        "-h",
        conn.host,
        "-P",
        str(conn.port),
        "-u",
        conn.user,
    ]
    if extra:
        cmd += extra
    return cmd


def query(conn, sql):
    proc = run(mysql_cmd(conn, ["-e", sql]), conn.password)
    return proc.stdout


def parse_jdbc(url):
    """解析 jdbc:mysql://[user:pass@]host[:port][/db][?params] 形式的连接地址。"""
    m = re.match(r"^jdbc:([a-z0-9]+)://(.*)$", url.strip(), re.IGNORECASE)
    if not m:
        raise DumpError("无法解析 JDBC 地址, 应以 jdbc:mysql:// 或 jdbc:mariadb:// 开头")
    rest = m.group(2)
    user = password = None
    if "@" in rest:
        auth, rest = rest.rsplit("@", 1)
        if ":" in auth:
            user, password = auth.split(":", 1)
        else:
            user = auth
    hostport_path, _, _query = rest.partition("?")
    hostport, _, db = hostport_path.partition("/")
    if not hostport:
        raise DumpError("JDBC 地址缺少 host")
    if hostport.startswith("["):
        end = hostport.find("]")
        if end == -1:
            raise DumpError("JDBC 地址 IPv6 写法错误")
        host = hostport[1:end]
        tail = hostport[end + 1 :]
        port = tail[1:] if tail.startswith(":") else None
    elif ":" in hostport:
        host, _, port = hostport.rpartition(":")
    else:
        host, port = hostport, None
    return {
        "host": host,
        "port": port or str(DEFAULT_PORT),
        "user": user,
        "password": password,
        "db": db or None,
    }


def parse_mysql_cmd(text):
    """解析 mysql 命令行中的 -h/--host、-P/--port、-u/--user、-p/--password 与库名。"""
    try:
        tokens = shlex.split(text)
    except ValueError as exc:
        raise DumpError(f"mysql 命令解析失败: {exc}") from exc
    if tokens and tokens[0] in ("mysql", "mysqldump"):
        tokens = tokens[1:]
    result = {"host": None, "port": None, "user": None, "password": None, "db": None}
    positional = []

    def value_of(flag):
        nonlocal i
        i += 1
        if i >= len(tokens):
            raise DumpError(f"参数 {flag} 缺少值")
        return tokens[i]

    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ("-h", "--host"):
            result["host"] = value_of(tok)
        elif tok.startswith("--host="):
            result["host"] = tok[len("--host=") :]
        elif tok.startswith("-h") and len(tok) > 2:
            result["host"] = tok[2:]
        elif tok in ("-P", "--port"):
            result["port"] = value_of(tok)
        elif tok.startswith("--port="):
            result["port"] = tok[len("--port=") :]
        elif tok.startswith("-P") and len(tok) > 2:
            result["port"] = tok[2:]
        elif tok in ("-u", "--user"):
            result["user"] = value_of(tok)
        elif tok.startswith("--user="):
            result["user"] = tok[len("--user=") :]
        elif tok.startswith("-u") and len(tok) > 2:
            result["user"] = tok[2:]
        elif tok in ("-p", "--password"):
            # 裸 -p 表示登录时交互输入密码, 脚本无法获取, 留给用户补充
            result["password"] = None
        elif tok.startswith("--password="):
            result["password"] = tok[len("--password=") :]
        elif tok.startswith("-p") and len(tok) > 2:
            result["password"] = tok[2:]
        else:
            if tok.startswith("-") and len(tok) > 1:
                i += 1
                continue
            positional.append(tok)
        i += 1

    if positional:
        result["db"] = positional[0]
    return result


def print_kv(result):
    for key, value in result.items():
        print(f"{key}={value if value is not None else ''}")


def get_tables(conn, db):
    """返回 [(表名, 类型)]; 类型为 BASE TABLE / VIEW。"""
    out = query(conn, f"SHOW FULL TABLES FROM {quote_ident(db)}")
    tables = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        name = parts[0]
        typ = parts[1] if len(parts) > 1 else "BASE TABLE"
        tables.append((name, typ))
    return tables


def get_count(conn, db, table):
    out = query(conn, f"SELECT COUNT(1) FROM {quote_ident(db)}.{quote_ident(table)}")
    return int(out.strip())


def extract_create_database(conn, db):
    """用 mysqldump --databases 只取 CREATE DATABASE 语句。"""
    dump = run(
        mysqldump_cmd(conn, ["--no-data", "--databases", db]), conn.password
    ).stdout
    m = re.search(r"CREATE DATABASE.*?;", dump, re.DOTALL)
    if m:
        return m.group(0).strip() + "\n"
    # 兜底: 从 information_schema 生成建库语句
    sql = (
        "SELECT CONCAT('CREATE DATABASE IF NOT EXISTS `', SCHEMA_NAME, '` "
        "DEFAULT CHARACTER SET ', "
        "DEFAULT_CHARACTER_SET_NAME, ' DEFAULT COLLATE ', DEFAULT_COLLATION_NAME, ';') "
        "FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = "
        f"'{db.replace(chr(39), chr(39) * 2)}'"
    )
    out = query(conn, sql).strip()
    if out:
        return out + "\n"
    raise DumpError(f"未能生成 {db} 的建库语句")


def dump_schema(conn, db, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    create_db = extract_create_database(conn, db)
    create_db_path = out_dir / "create-db.sql"
    create_db_path.write_text(create_db, encoding="utf-8")
    print(f"[OK] {create_db_path} ({len(create_db.encode('utf-8'))} 字节, 仅建库语句)")

    schema = run(
        mysqldump_cmd(conn, ["--no-data", "--routines", "--triggers", db]),
        conn.password,
    ).stdout
    schema_path = out_dir / "schema.sql"
    schema_path.write_text(schema, encoding="utf-8")
    print(
        f"[OK] {schema_path} ({len(schema.encode('utf-8'))} 字节, "
        "含结构/存储过程/函数/触发器/索引)"
    )
    return create_db_path, schema_path


def write_placeholder(out_dir, table, note):
    path = out_dir / f"dump_data_{safe_component(table)}_count_0.sql"
    path.write_text(f"-- {note}\n", encoding="utf-8")
    print(f"[SKIP] {path} ({note})")
    return path


def dump_table(conn, db, table, out_dir, max_rows):
    count = get_count(conn, db, table)
    if count == 0 or count > max_rows:
        reason = "行数为 0" if count == 0 else f"行数 {count} 超过阈值 {max_rows}"
        return write_placeholder(out_dir, table, f"表 {table} {reason}, 跳过数据导出")
    data = run(
        mysqldump_cmd(
            conn,
            [
                "--compact",
                "--no-create-info",
                "--skip-triggers",
                "--single-transaction",
                "--complete-insert",
                "--extended-insert",
                db,
                table,
            ],
        ),
        conn.password,
    ).stdout
    path = out_dir / f"dump_data_{safe_component(table)}_count_{count}.sql"
    path.write_text(data, encoding="utf-8")
    print(f"[OK] {path} (表 {table}, {count} 行)")
    return path


def dump_all(conn, db, out_dir, max_rows):
    dump_schema(conn, db, out_dir)
    tables = get_tables(conn, db)
    print(f"共 {len(tables)} 张表/视图:")
    for table, typ in tables:
        if typ != "BASE TABLE":
            write_placeholder(out_dir, table, f"视图 {table} 无数据, 跳过数据导出")
            continue
        dump_table(conn, db, table, out_dir, max_rows)


def build_conn(args):
    password = args.password
    if password is None:
        password = os.environ.get(MYSQL_PWD_ENV)
    if password is None:
        raise DumpError(
            f"未提供数据库密码: 请通过环境变量 {MYSQL_PWD_ENV} 传入, "
            "或使用 --password 参数"
        )
    return argparse.Namespace(
        host=args.host,
        port=args.port,
        user=args.user,
        password=password,
    )


def add_conn_args(parser):
    parser.add_argument("--host", required=True, help="数据库服务 host IP 或域名")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="数据库端口, 默认 3306")
    parser.add_argument("--user", required=True, help="数据库登录用户名")
    parser.add_argument(
        "--password",
        default=None,
        help="数据库密码 (优先使用环境变量 MYSQL_PWD, 避免明文出现在命令行)",
    )


def main():
    parser = argparse.ArgumentParser(
        prog="dump_mysql.py",
        description="smart-dump-mysql-skill 执行脚本",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("verify", help="校验登录并列出可访问数据库")
    add_conn_args(p)

    p = sub.add_parser("parse-jdbc", help="解析 JDBC 连接地址")
    p.add_argument("url", help='如 jdbc:mysql://10.0.0.1:3306/mydb?useSSL=false')

    p = sub.add_parser("parse-mysql-cmd", help="解析 mysql 命令行")
    p.add_argument("cmd_text", help='如 mysql -h 10.0.0.1 -P 3306 -u root -p mydb')

    p = sub.add_parser("list-tables", help="列出数据库中的全部表")
    add_conn_args(p)
    p.add_argument("--db", required=True, help="数据库名")

    p = sub.add_parser("count", help="查询单表行数")
    add_conn_args(p)
    p.add_argument("--db", required=True, help="数据库名")
    p.add_argument("--table", required=True, help="表名")

    p = sub.add_parser("dump-schema", help="导出 create-db.sql 与 schema.sql")
    add_conn_args(p)
    p.add_argument("--db", required=True, help="数据库名")
    p.add_argument("--out-dir", default=None, help="输出目录, 默认 dump-result/{数据库名}")

    p = sub.add_parser("dump-table", help="按行数阈值导出单表数据")
    add_conn_args(p)
    p.add_argument("--db", required=True, help="数据库名")
    p.add_argument("--table", required=True, help="表名")
    p.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS, help="行数阈值, 默认 3000")
    p.add_argument("--out-dir", default=None, help="输出目录, 默认 dump-result/{数据库名}")

    p = sub.add_parser("dump-all", help="导出结构与全部表数据")
    add_conn_args(p)
    p.add_argument("--db", required=True, help="数据库名")
    p.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS, help="行数阈值, 默认 3000")
    p.add_argument("--out-dir", default=None, help="输出目录, 默认 dump-result/{数据库名}")

    args = parser.parse_args()

    try:
        if args.command == "parse-jdbc":
            print_kv(parse_jdbc(args.url))
            return
        if args.command == "parse-mysql-cmd":
            print_kv(parse_mysql_cmd(args.cmd_text))
            return

        conn = build_conn(args)
        if args.command == "verify":
            out = query(conn, "SHOW DATABASES;")
            dbs = [line.strip() for line in out.splitlines() if line.strip()]
            print(f"登录成功, 可访问 {len(dbs)} 个数据库:")
            for db in dbs:
                print(f"  {db}")
            return

        db = args.db
        out_dir = Path(args.out_dir) if args.out_dir else Path("dump-result") / safe_component(db)
        if args.command == "list-tables":
            for table, typ in get_tables(conn, db):
                print(f"{table}\t{typ}")
            return
        if args.command == "count":
            print(get_count(conn, db, args.table))
            return
        if args.command == "dump-schema":
            dump_schema(conn, db, out_dir)
            return
        if args.command == "dump-table":
            dump_table(conn, db, args.table, out_dir, args.max_rows)
            return
        if args.command == "dump-all":
            dump_all(conn, db, out_dir, args.max_rows)
            return
    except DumpError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
