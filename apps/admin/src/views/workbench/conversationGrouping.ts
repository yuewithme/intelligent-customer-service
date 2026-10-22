import type { ConversationItem } from '@/api/admin/conversations'

export interface ConversationGroupItem extends ConversationItem {
  group_key: string
  conversation_ids: string[]
  grouped_count: number
}

export const conversationGroupKey = (item: ConversationItem) =>
  [item.tenant_id, item.channel, item.user_id].join('::')

const byUpdatedDesc = (left: ConversationItem, right: ConversationItem) =>
  new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime()

const byRelevance = (left: ConversationItem, right: ConversationItem) =>
  (right.search_score || 0) - (left.search_score || 0)

export const groupConversationsByCustomer = (
  conversations: ConversationItem[],
  options: { collapseTestData?: boolean } = {}
): ConversationGroupItem[] => {
  if (options.collapseTestData && conversations.length) {
    const ordered = [...conversations].sort((left, right) => byRelevance(left, right) || byUpdatedDesc(left, right))
    const latest = ordered[0]
    return [
      {
        ...latest,
        group_key: '__test_data__',
        conversation_ids: ordered.map((item) => item.conversation_id),
        grouped_count: ordered.length,
        user_id: '__test_data__',
        user_display_name: `测试数据（${ordered.length} 个会话）`,
        user_avatar_url: null,
        last_message: '全部测试会话已收纳到此组',
        unread_count: ordered.reduce((total, item) => total + item.unread_count, 0)
      }
    ]
  }

  const groups = new Map<string, ConversationItem[]>()

  for (const conversation of conversations) {
    const key = conversationGroupKey(conversation)
    const group = groups.get(key)
    if (group) {
      group.push(conversation)
    } else {
      groups.set(key, [conversation])
    }
  }

  return Array.from(groups.entries())
    .map(([group_key, group]) => {
      const ordered = [...group].sort((left, right) => byRelevance(left, right) || byUpdatedDesc(left, right))
      const latest = ordered[0]

      return {
        ...latest,
        group_key,
        conversation_ids: ordered.map((item) => item.conversation_id),
        grouped_count: ordered.length,
        unread_count: ordered.reduce((total, item) => total + item.unread_count, 0),
        is_pinned: ordered.some((item) => item.is_pinned),
        is_muted: ordered.every((item) => item.is_muted),
        marked_unread: ordered.some((item) => item.marked_unread)
      }
    })
    .sort((left, right) => {
      if (left.search_score !== undefined || right.search_score !== undefined) {
        return byRelevance(left, right) || byUpdatedDesc(left, right)
      }
      return Number(right.is_pinned) - Number(left.is_pinned) || byUpdatedDesc(left, right)
    })
}
