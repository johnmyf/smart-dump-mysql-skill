# 智能mysql dump技能
生成技能 smart-dump-mysql-skill, 必须显示调用, 不能自触发

技能执行循序
- 必备条件
- 检查环境
- 获取数据库连接
- 导出数据库结构
- 导出数据
  

## 必备条件
mysql和mysqldump 使用默认字符串: --default-character-set=utf8mb4

## 检查环境
- 本地必须具备mysql-client(需要用到mysql和mysqldump命令) , 如果本地没有, 提示用户安装, 如果用户同意, 则直接安装mysql-client, 并帮助其配置好运行环境.

## 获取数据库连接
- 让用户选择输入数据库信息的办法
  1. 手动输入数据库权限信息(输入1)
  2. 直接输入JDBC数据库连接命令
  3. 直接输入链接数据库的mysql命令
如用户选择1, 或输入JDBC或mysql命令后, 缺少必要的参数, 通过如下提示, 让用户补充信息
- 让用户输入数据库服务的host ip或域名
- 让用户输入数据库服务的端口
- 让用户输入数据库登录用户名
- 让用户输入数据库密码
- 用mysql命令登录坚持信息是否正确, 不正确则重新输入, 并用show databases;命令获取用户能访问的数据库, 提示用户可访问数据库列表, 并让用户选择一个需要dump数据的数据库. 我们用{数据库名}来表示这个数据的名称
  
## 导出数据库结构
- 使用mysqldump命令,  只生成建库语句(create database语句), 保存到 dump-result/{数据库名}/create-db.sql
- 使用mysqldump命令,  用参数--no-data / -d , 只导结构，不导数据​, 包含存储过程和函数, 包含触发器, 包含表索引, 保存到 dump-result/{数据库名}/schema.sql

## 导出数据
- 使用 show tables, 获取所有表格, 我们开始遍历所有表格
- 循环每个table, 设定本次循环的表格名为:{table_name}
  1. 通过select count(1) from {table_name}; 获得表的数据总数, 我们用{table_count}表示
  2. 如果{table_count} > 3000 or {table_count} =0 , 则跳过, 不导数据, 创建: dump-result/{数据库名}/dump_data_{table_name}_count_0.sql
  3. 如果{table_count} > 0 && {table_count} <= 3000, 则导出这个表格的数据(不含建表命令, 只含数据批量插入命令), 保存到 dump-result/{数据库名}/dump_data_{table_name}_count_{table_count}.sql



