---
name: smart-dump-mysql-skill
description: 智能导出 MySQL 数据库。检查 mysql/mysqldump 环境，交互获取并校验连接信息，导出建库语句、数据库结构（含存储过程/函数、触发器、索引）及按行数阈值分表的数据文件。仅在用户显式调用 $smart-dump-mysql-skill 时使用，不自动触发。
---

# 智能 MySQL Dump

严格按以下顺序执行：必备条件 → 检查环境 → 获取数据库连接 → 导出数据库结构 → 导出数据。所有 mysql / mysqldump 调用一律加 `--default-character-set=utf8mb4`。输出统一放在当前工作目录的 `dump-result/{数据库名}/` 下。

## 1. 必备条件

需要可用的 `mysql` 和 `mysqldump` 命令，字符集固定为 `--default-character-set=utf8mb4`。

## 2. 检查环境

1. 检查 `mysql` 和 `mysqldump` 是否可用：`command -v mysql`、`command -v mysqldump`。
2. 如果本地没有，先提示用户并征得同意后再安装 mysql-client：
   - macOS：`brew install mysql-client`（keg-only，装完后把 `$(brew --prefix mysql-client)/bin` 加入 PATH，并建议写入 `~/.zshrc` 等 shell 配置，让 `mysql`、`mysqldump` 全局可用）。
   - Debian/Ubuntu：`sudo apt-get update && sudo apt-get install -y default-mysql-client`。
   - RHEL/Fedora/CentOS：`sudo dnf install -y mysql`。
   - 其他系统按对应包管理器安装 mysql-client。
3. 用户不同意安装则停止并说明原因。安装完成后验证 `mysql --version` 和 `mysqldump --version` 能正常输出，即视为运行环境配置完成。

## 3. 获取数据库连接

让用户选择输入数据库信息的方式：

1. 手动输入数据库权限信息（输入 1）
2. 直接输入 JDBC 数据库连接命令，如 `jdbc:mysql://10.0.0.1:3306/mydb?useSSL=false`
3. 直接输入连接数据库的 mysql 命令，如 `mysql -h 10.0.0.1 -P 3306 -u root -p mydb`

选 2 或 3 时先用脚本解析参数：`python3 scripts/dump_mysql.py parse-jdbc "JDBC地址"` 或 `python3 scripts/dump_mysql.py parse-mysql-cmd "mysql命令"`。无论哪种方式，只要缺少下列参数之一，就逐一提示用户补充：

- 数据库服务的 host IP 或域名
- 数据库服务的端口
- 数据库登录用户名
- 数据库密码

然后用 mysql 命令验证登录（密码通过环境变量 `MYSQL_PWD` 传入，避免出现在命令行参数或进程列表中）：

```bash
MYSQL_PWD='密码' mysql --default-character-set=utf8mb4 --connect-timeout=10 \
  -h host -P port -u 用户名 -e "SHOW DATABASES;"
```

登录失败则提示重新输入，直到成功。用 `SHOW DATABASES;` 的结果列出用户可访问的数据库，让用户选择要 dump 的数据库，记为 {数据库名}。

## 4. 导出数据库结构

目标目录 `dump-result/{数据库名}/`，生成两个文件：

- `create-db.sql`：只含建库语句（CREATE DATABASE）。执行 `mysqldump --default-character-set=utf8mb4 --no-data --databases {数据库名}`，从输出中只保留 CREATE DATABASE 语句写入该文件。
- `schema.sql`：只导结构不导数据（`--no-data` / `-d`），包含存储过程和函数（`--routines`）、触发器（`--triggers`）、表索引（随 CREATE TABLE 导出）：`mysqldump --default-character-set=utf8mb4 --no-data --routines --triggers {数据库名}`，输出写入该文件。

## 5. 导出数据

1. 用 `SHOW FULL TABLES FROM {数据库名}` 获取全部表（含类型，视图不算表）。
2. 遍历每张表，本次循环的表名记为 {table_name}：
   - 通过 `SELECT COUNT(1) FROM {数据库名}.{table_name};` 获得数据总数，记为 {table_count}。
   - 若 {table_count} > 3000 或 {table_count} = 0：跳过，不导数据，创建占位文件 `dump-result/{数据库名}/dump_data_{table_name}_count_0.sql`（内容仅为说明注释，无 INSERT）。
   - 若 0 < {table_count} <= 3000：导出该表数据（不含建表命令，只含批量 INSERT 命令），保存到 `dump-result/{数据库名}/dump_data_{table_name}_count_{table_count}.sql`。命令示例：

     ```bash
     mysqldump --default-character-set=utf8mb4 --no-create-info --skip-triggers \
       --skip-add-drop-table --single-transaction --quick --complete-insert --extended-insert \
       {数据库名} {table_name}
     ```
3. 视图没有可导出的数据，一律按 count_0 占位处理，不执行数据导出。

## 执行方式

优先使用脚本完成第 4、5 步（密码通过 `MYSQL_PWD` 环境变量传入，不要写在命令行里）：

```bash
MYSQL_PWD='密码' python3 scripts/dump_mysql.py dump-all \
  --host host --port 3306 --user 用户名 --db {数据库名}
```

脚本会依次执行：生成 `create-db.sql`、`schema.sql`，遍历全部表并按 3000 行阈值决定跳过或导出数据，最后打印每张表的处理结果。也可分步使用子命令 `verify`（校验登录并列出数据库）、`list-tables`、`count`、`dump-schema`、`dump-table`。阈值可用 `--max-rows` 调整，默认 3000。

全部完成后，检查 `dump-result/{数据库名}/` 下文件的命名与内容是否符合上述要求，并向用户汇报：导出了哪些文件、每张表的数据量、哪些表因超阈值或为空被跳过。
