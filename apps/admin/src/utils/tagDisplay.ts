const INTENT_TEXT: Record<string, string> = {
  greeting: '问候',
  profile_answer: '画像信息回答',
  ask_price: '询问价格',
  price_objection: '价格异议',
  discount_request: '优惠诉求',
  ask_logistics: '物流咨询',
  ask_after_sale: '售后咨询',
  order_intent: '下单意向',
  payment_intent: '付款意向',
  order_query: '订单查询',
  product_query: '商品查询',
  purchase_rejection: '拒绝购买',
  knowledge_question: '知识咨询',
  care_question: '养护问题',
  process_question: '流程咨询',
  usage_question: '使用咨询',
  refund_request: '退款申请',
  complaint: '投诉',
  human_request: '转人工',
  message: '普通消息',
  unsupported: '非业务问题',
  unknown: '待识别'
}

const SALES_STAGE_TEXT: Record<string, string> = {
  unknown: '待识别',
  rapport: '破冰',
  need_discovery: '挖需求',
  pain_discovery: '找痛点',
  solution_recommended: '推品',
  value_built: '塑品',
  trial_close: '试成交',
  closing: '成交推进',
  after_sale: '售后服务',
  human_pending: '等待人工',
  // Historical values are display-only aliases and must not be written by new flows.
  greeting: '破冰',
  pain_confirmed: '找痛点',
  price_discussed: '试成交',
  objection_handling: '成交推进',
  order_intent: '成交推进',
  interest: '挖需求',
  knowledge_consulting: '挖需求',
  care_support: '挖需求',
  first_order_nurture: '挖需求'
}

const SEGMENT_TEXT: Record<string, string> = {
  unknown: '待识别',
  beginner: '新手客户',
  advanced: '进阶客户'
}

const SENTIMENT_TEXT: Record<string, string> = {
  neutral: '平静',
  anxious: '焦虑',
  angry: '愤怒'
}

const RISK_TEXT: Record<string, string> = {
  normal: '正常',
  low: '低风险',
  medium: '中风险',
  high: '高风险',
  elevated: '已升级处理'
}

const TAG_PREFIX_TEXT: Record<string, Record<string, string>> = {
  intent: INTENT_TEXT,
  stage: SALES_STAGE_TEXT,
  segment: SEGMENT_TEXT,
  emotion: SENTIMENT_TEXT,
  risk: RISK_TEXT
}

export const intentText = (value?: string | null) =>
  value ? INTENT_TEXT[value] || value : '-'

export const salesStageText = (value?: string | null) =>
  value ? SALES_STAGE_TEXT[value] || value : '-'

export const riskLevelText = (value?: string | null) =>
  value ? RISK_TEXT[value] || value : '-'

export const tagValueText = (value: string) => {
  const separator = value.indexOf(':')
  if (separator < 0) return value
  const prefix = value.slice(0, separator)
  const rawValue = value.slice(separator + 1)
  return TAG_PREFIX_TEXT[prefix]?.[rawValue] || rawValue
}

const ORCHID_TEXT: Record<string, string> = {
  chunlan: '春兰',
  jianlan: '建兰',
  molan: '墨兰',
  hanlan: '寒兰',
  huilan: '蕙兰',
  lianbanlan: '莲瓣兰',
  chunjian: '春剑',
  cymbidium: '大花蕙兰'
}

const REGION_TEXT: Record<string, string> = {
  east_china: '华东地区',
  north_china: '华北地区',
  south_china: '华南地区',
  southwest: '西南地区',
  northwest: '西北地区'
}

export const promptTitleText = (blockId: string, fallback: string) => {
  const levelMatch = blockId.match(/^customer_level\.(l[1-6])\.(identity|communication|recommendation)$/)
  if (levelMatch) {
    const purpose = { identity: '身份策略', communication: '沟通策略', recommendation: '推荐策略' }[levelMatch[2]]
    return `${levelMatch[1].toUpperCase()} 客户${purpose}`
  }
  const preferenceMatch = blockId.match(/^orchid_preference\.(.+)$/)
  if (preferenceMatch) return `${ORCHID_TEXT[preferenceMatch[1]] || preferenceMatch[1]}偏好策略`
  const quantityMatch = blockId.match(/^orchid_quantity\.(small|medium|large)\.focus$/)
  if (quantityMatch) {
    return `${{ small: '少量养兰', medium: '中等规模养兰', large: '大规模养兰' }[quantityMatch[1]]}策略`
  }
  const regionMatch = blockId.match(/^region\.(.+)\.variety$/)
  if (regionMatch) return `${REGION_TEXT[regionMatch[1]] || regionMatch[1]}品种推荐策略`
  return fallback
}
