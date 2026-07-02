import { defineStore } from 'pinia'
import { store } from '../index'
import type { ElementPlusInfoType } from '@/types/elementPlus'

export interface DictValueType {
  dictType: string
  value: any
  label: string
  colorType: ElementPlusInfoType | ''
  cssClass: string
}

export interface DictState {
  dictMap: Record<string, DictValueType[]>
  isSetDict: boolean
}

export const useDictStore = defineStore('dict', {
  state: (): DictState => ({
    dictMap: {},
    isSetDict: true
  }),
  getters: {
    getDictMap(): Record<string, DictValueType[]> {
      return this.dictMap
    },
    getIsSetDict(): boolean {
      return this.isSetDict
    }
  },
  actions: {
    async setDictMap() {
      this.isSetDict = true
    },
    getDictByType(type: string) {
      return this.dictMap[type] || []
    },
    async resetDict() {
      this.dictMap = {}
      this.isSetDict = true
    }
  }
})

export const useDictStoreWithOut = () => {
  return useDictStore(store)
}
