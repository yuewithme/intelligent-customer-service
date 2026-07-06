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

export const groupConversationsByCustomer = (
  conversations: ConversationItem[]
): ConversationGroupItem[] => {
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
      const ordered = [...group].sort(byUpdatedDesc)
      const latest = ordered[0]

      return {
        ...latest,
        group_key,
        conversation_ids: ordered.map((item) => item.conversation_id),
        grouped_count: ordered.length,
        unread_count: ordered.reduce((total, item) => total + item.unread_count, 0)
      }
    })
    .sort(byUpdatedDesc)
}
