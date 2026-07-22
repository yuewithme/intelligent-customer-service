import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const dir = path.dirname(fileURLToPath(import.meta.url));
const expected = {
  "single_turn.jsonl": 30,
  "multi_turn.jsonl": 10,
  "boundary.jsonl": 15
};

const capabilityPattern = /^[UQKSOG]\d+$/;
const stagePattern = /^P[1-8]$/;
const taxonomy = fs.readFileSync(path.join(dir, "..", "02_capability_taxonomy.md"), "utf8");
const definedCapabilities = new Set(
  [...taxonomy.matchAll(/^## ([UQKSOG]\d+) /gm)].map((match) => match[1])
);
const ids = new Set();
const errors = [];
let total = 0;
const singleCases = new Map();
const multiCases = new Map();

function readJsonl(file) {
  return fs.readFileSync(path.join(dir, file), "utf8")
    .trim()
    .split(/\r?\n/)
    .map((line, index) => {
      try {
        return JSON.parse(line);
      } catch (error) {
        errors.push(`${file}:${index + 1} JSON解析失败: ${error.message}`);
        return null;
      }
    })
    .filter(Boolean);
}

for (const [file, count] of Object.entries(expected)) {
  const items = readJsonl(file);
  if (items.length !== count) errors.push(`${file} 应为${count}条，实际${items.length}条`);
  total += items.length;

  for (const item of items) {
    if (!item.id) errors.push(`${file} 存在缺少id的题`);
    if (ids.has(item.id)) errors.push(`重复id: ${item.id}`);
    ids.add(item.id);

    if (!["real", "derived"].includes(item.source_type)) errors.push(`${item.id} source_type无效`);
    if (item.source_type === "real" && !/^case(0[1-9]|10)$/.test(item.source_case)) errors.push(`${item.id} source_case无效`);
    if (item.source_type === "derived" && !/^case(0[1-9]|10)$/.test(item.derived_from)) errors.push(`${item.id} derived_from无效`);

    if (item.task_type === "multi_turn") {
      multiCases.set(item.source_case, (multiCases.get(item.source_case) ?? 0) + 1);
      if (!Array.isArray(item.customer_turns) || item.customer_turns.length < 2) errors.push(`${item.id} 缺少多轮客户消息`);
      if (!Array.isArray(item.success_criteria) || !item.success_criteria.length) errors.push(`${item.id} 缺少成功标准`);
      if (!Array.isArray(item.capabilities) || item.capabilities.some((code) => !definedCapabilities.has(code))) {
        errors.push(`${item.id} 多轮能力代码未在能力地图定义`);
      }
      continue;
    }

    if (item.task_type === "single_turn") {
      singleCases.set(item.source_case, (singleCases.get(item.source_case) ?? 0) + 1);
    }
    if (!stagePattern.test(item.stage)) errors.push(`${item.id} stage无效`);
    if (!capabilityPattern.test(item.primary_capability) || !definedCapabilities.has(item.primary_capability)) errors.push(`${item.id} 主能力无效`);
    if (!Array.isArray(item.secondary_capabilities) || item.secondary_capabilities.some((code) => !capabilityPattern.test(code) || !definedCapabilities.has(code))) {
      errors.push(`${item.id} 辅助能力无效`);
    }
    if (!Array.isArray(item.conversation) || item.conversation.at(-1)?.role !== "user") errors.push(`${item.id} 对话必须以客户消息结束`);
    if (!Array.isArray(item.must_have) || item.must_have.length < 2) errors.push(`${item.id} must_have不足`);
    if (!Array.isArray(item.must_not) || !item.must_not.length) errors.push(`${item.id} must_not不足`);
    if (!item.reference_answer) errors.push(`${item.id} 缺少参考回答`);

    const score = item.scoring ?? {};
    if (score.must_have_points + score.should_have_points + score.expression_points !== 100) {
      errors.push(`${item.id} 基础分不等于100`);
    }
  }
}

if (total !== 55) errors.push(`总题数应为55，实际${total}`);
for (const caseNumber of Array.from({ length: 10 }, (_, index) => `case${String(index + 1).padStart(2, "0")}`)) {
  if (singleCases.get(caseNumber) !== 3) errors.push(`${caseNumber} 应有3道真实单轮题`);
  if (multiCases.get(caseNumber) !== 1) errors.push(`${caseNumber} 应有1道真实多轮题`);
}

if (errors.length) {
  console.error(errors.join("\n"));
  process.exit(1);
}

console.log(`VALIDATION_OK files=3 items=${total} unique_ids=${ids.size}`);
