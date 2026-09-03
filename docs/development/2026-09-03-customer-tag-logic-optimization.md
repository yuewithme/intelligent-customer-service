# 客户标签库及生效逻辑优化

## 任务目标

在现有动态标签目录、客户画像、Sales Agent 和商品搜索能力上完成最小改造：修正重叠标签，补齐产品需求、价格接受范围和养兰环境，使 AI 能依据客户原话写入准确标签，并让标签稳定影响后续回复与商品匹配。

## 实施计划

1. 升级现有标签目录，调整单选/多选属性并安全迁移旧标签值。
2. 扩展 `customer.tag` 的发现范围和证据化打标说明，不新增独立识别服务。
3. 将现有标签关联提示词接入当前自主 Agent 工作区。
4. 在现有 `product.search` 中复用客户标签补全搜索条件，保持客户本轮明确需求优先。
5. 更新直接相关测试，验证目录升级、标签替换/共存、提示词生效和商品匹配。
6. 检查最终差异，提交并推送 GitLab `main`。

## 完成结果

- 将养兰数量调整为无重叠区间，并补齐准备养兰、500-999盆和1000盆以上。
- 标准化广西标签，补齐港澳台、海外、豆瓣兰和小众品类。
- 新增产品需求分类、价格接受范围和养兰环境，按业务含义配置单选或多选。
- 复用目录版本升级机制迁移旧盆数、旧产品需求、旧价格和旧环境标签，同时迁移客户画像与提示词绑定。
- 扩展 `customer.tag` 的能力发现词和证据化写入说明，支持一条客户消息记录多个明确标签。
- 将现有标签提示词绑定接入当前自主 Agent 工作区；补齐 L4-L6 提示词，并取消等级本身触发人工的旧行为。
- 在现有商品搜索入口中补入未被本轮明确条件覆盖的画像标签；“不限”不形成筛选，本轮新条件优先。
- 喜欢的兰花品类为多选时，商品搜索按任一已选品类匹配，不按固定顺序误缩成单一品类。
- 复用现有商品字段和文本匹配，没有新增商品表字段或独立标签识别服务。
- 后台标签选择界面继续复用动态单选/多选渲染，仅补充新提示词名称的中文显示。

## 验证情况

- `python -m pytest apps/api/tests/test_tag_catalog.py apps/api/tests/test_business_tag_prompt_policy.py apps/api/tests/test_admin_tags.py apps/api/tests/test_user_profile_api.py apps/api/tests/test_chat_orchestrator_workspace.py apps/api/tests/test_admin_products.py apps/api/tests/test_policy_engine.py apps/api/tests/test_agent_harness_v2.py::test_customer_tag_records_evidence_backed_catalog_tag apps/api/tests/test_agent_harness_v2.py::test_product_search_adds_profile_tags_and_respects_current_overrides -q`：53 项通过。
- `pnpm ts:check`：通过。
- Python 目标文件语法检查：通过。
- `ruff` 未安装，因此未执行该可选检查。
