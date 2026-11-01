import { describe, expect, it } from 'vitest'
import { sharedPrefix } from '../components/askShared'

describe('定稿接着初稿打字', () => {
  it('定稿只多了尾巴: 从初稿末尾接着打', () => {
    expect(sharedPrefix('甲乙丙', '甲乙丙丁')).toBe(3)
  })
  it('定稿插了联网角标: 角标不打断前缀, 下标按定稿算', () => {
    const draft = '核心零部件业务[联网]。\n2. 合作'
    const full = '核心零部件业务⟦24⟧[联网]。\n2. 合作⟦3⟧⟦12⟧继续'
    expect(sharedPrefix(draft, full)).toBe(full.indexOf('继续'))
  })
  it('正文真的改了: 前缀停在改动处', () => {
    expect(sharedPrefix('今天涨了', '今天跌了')).toBe(2)
  })
})
