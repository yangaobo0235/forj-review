"""Explicit versioned schema setup. Normal startup is read-only."""

from psycopg import sql

from forj_review_database.migrations import migration_history
from forj_review_database.schema import BASELINE_VERSION, SCHEMA_SQL, schema_checksum


def initialize_schema(connection):
    """Caller must choose an empty target schema and own the migration transaction."""
    connection.execute(SCHEMA_SQL.read_text(encoding="utf-8"), prepare=False)
    connection.execute("INSERT INTO sys_schema_migration(version,name,checksum) VALUES(%s,%s,%s)",
                       (BASELINE_VERSION, "relational_page_configuration", schema_checksum()))
    for migration in migration_history()[1:]:
        connection.execute(migration.read_sql(), prepare=False)
        connection.execute(
            "INSERT INTO sys_schema_migration(version,name,checksum) VALUES(%s,%s,%s)",
            (migration.version, migration.name, migration.checksum),
        )
    apply_schema_comments(connection)


def apply_schema_comments(connection):
    """Describe application-owned tables only; protected business tables stay untouched."""
    comments = {
        "cfg_page": "审核页面稳定身份", "cfg_page_version": "不可变的完整页面配置版本",
        "cfg_page_field": "页面版本的字段声明", "cfg_page_material": "页面版本的材料声明",
        "cfg_release": "配置发布与回滚记录", "cfg_release_item": "发布批次的页面版本选择",
        "cfg_head": "当前发布指针与并发修订号", "cfg_capture": "页面现场采集",
        "cfg_validation_run": "不可变的已完成配置验证证据",
        "cfg_session": "配置维护会话、草稿与执行状态", "cfg_session_event": "配置会话进度事件",
        "cfg_idempotency": "配置请求幂等凭据", "ref_vehicle_model_key": "车型规范化查询键",
        "ref_engine_emission": "发动机型号到唯一国三至国六值的参考映射",
        "ref_dataset_import": "参考数据导入来源与核验记录", "sys_schema_migration": "应用数据库迁移记录",
    }
    for table, comment in comments.items():
        connection.execute(sql.SQL("COMMENT ON TABLE {} IS {}").format(sql.Identifier(table), sql.Literal(comment)))
    labels = {
        "id":"记录主键", "page_code":"稳定页面编码", "business_type":"审核业务类型", "region":"页面地区编码",
        "page_id":"所属审核页面主键", "version_no":"页面配置业务版本号", "schema_version":"页面声明契约版本",
        "title":"当前版本的页面标题", "page_options":"页面匹配、策略及能力声明；不含字段与材料列表",
        "content_digest":"完整页面声明的 SHA-256 摘要", "created_by":"创建操作者标识",
        "created_at":"创建时间（UTC 语义）", "page_version_id":"所属或选择的页面版本主键",
        "field_key":"字段稳定编码", "label":"显示名称", "sort_order":"版本内部的声明顺序，从零开始",
        "reviewable":"是否参与审核", "required":"是否要求提供", "writable":"是否允许回填",
        "normalizer":"已登记的规范化器编码", "options":"有类型约束的嵌套声明，不重复存储已分列属性",
        "material_code":"材料类型编码", "minimum_count":"最少材料数量", "release_no":"发布批次唯一编号",
        "base_release_id":"发布所基于的前一批次", "rollback_target_release_id":"整批回滚的目标历史发布",
        "operation_type":"操作类型", "source":"维护入口来源", "source_session_id":"发起发布的配置会话，可为空",
        "operation_id":"发布幂等操作标识，永久唯一", "request_digest":"请求内容摘要",
        "manifest_digest":"本批页面版本选择及内容的摘要", "validation_summary":"校验摘要与持久化回执",
        "release_id":"选择或所属的发布批次", "revision":"乐观并发修订号", "updated_at":"最后更新时间",
        "extension_id":"发起采集的扩展标识", "page_url":"采集页面地址", "structure_digest":"页面结构摘要",
        "snapshot":"现场采集对象，不含原图二进制", "capture_id":"关联采集主键", "base_page_version_id":"草稿基线页面版本",
        "state":"配置会话业务状态", "turn_status":"助手回合执行状态", "draft":"可编辑的单页候选配置",
        "messages":"会话消息数组", "workflow":"验证、交互及执行状态，不重复存储会话关系列",
        "seq":"事件递增序号，允许间隙", "session_id":"关联配置会话", "kind":"事件类型", "payload":"事件对象载荷",
        "scope_key":"请求作用对象，与操作类型分开保存", "request_id":"客户端幂等请求标识",
        "result":"首次成功操作返回对象", "expires_at":"普通请求幂等凭据的保留截止时间",
        "model_id":"车型业务主键", "announcement_code":"规范化整车型号", "engine_code":"规范化发动机型号",
        "engine_model":"规范化完整发动机型号，保留后缀", "emission_standard":"该型号唯一有效的国三至国六排放值",
        "source_config_count":"支持映射的源车型配置条数", "dataset_code":"参考数据集编码", "source_digest":"源数据摘要",
        "format_version":"数据集格式版本", "row_count":"本批导入记录数", "metadata":"来源、生成规则及完整性核验对象",
        "imported_at":"导入批次时间", "version":"数据库迁移版本", "name":"迁移名称", "checksum":"迁移脚本摘要",
        "applied_at":"数据库迁移执行时间",
        "region_code":"该版本所属地区", "enabled":"该页面版本是否允许运行",
        "digest_version":"内容摘要算法契约", "change_reason":"本次配置变更说明",
        "guidance":"材料识别与核对指引", "validation_run_id":"发布依据的完成验证记录",
        "minimum_kernel_version":"执行该发布所需的最低框架契约版本",
        "confirmed_by":"确认发布的操作者", "confirmed_at":"确认发布时间",
        "snapshot_id":"浏览器现场编号", "page_instance_id":"浏览器页面实例编号",
        "snapshot_schema_version":"现场载荷契约版本", "baseline_digest":"固定配置基线摘要",
        "draft_digest":"当前草稿摘要", "workflow_schema_version":"流程载荷契约版本",
        "current_validation_run_id":"当前草稿完成验证记录", "published_release_id":"已完成的发布记录",
        "closed_at":"会话结束时间", "purpose":"验证用途：发布、回滚或维护导入",
        "candidate_page_id":"验证的候选页面；整批导入或回滚可空",
        "candidate_revision":"验证时的草稿修订号", "candidate_digest":"验证候选及内核绑定摘要",
        "scene_digest":"验证现场摘要", "kernel_version":"验证所使用的内核版本标识",
        "kernel_fingerprint":"验证内核源码指纹", "report_schema_version":"验证报告契约版本",
        "status":"完成验证结论", "report":"有容量与内容约束的完成验证报告",
        "performed_by":"执行验证的操作者", "started_at":"验证开始时间", "completed_at":"验证完成时间",
        "role":"消息角色", "content":"消息正文", "tool_call_id":"工具调用编号",
        "tool_name":"工具名称", "payload_schema_version":"事件载荷契约版本",
        "source_name":"参考数据来源名称", "source_environment":"参考数据来源环境",
        "source_exported_at":"源数据导出时间", "source_tables":"源业务表清单",
        "builder_version":"派生数据构建算法版本", "source_row_count":"参与构建的源记录数",
        "excluded_ambiguous_count":"排除的多义记录数", "excluded_invalid_count":"排除的无效记录数",
        "imported_by":"数据导入操作者",
    }
    for table in comments:
        columns = connection.execute("SELECT attname FROM pg_attribute WHERE attrelid=to_regclass(%s) "
                                     "AND attnum>0 AND NOT attisdropped", (table,)).fetchall()
        for (column,) in columns:
            connection.execute(sql.SQL("COMMENT ON COLUMN {}.{} IS {}").format(
                sql.Identifier(table),sql.Identifier(column),sql.Literal(labels.get(column, column))))
