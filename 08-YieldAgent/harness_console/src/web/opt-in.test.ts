import { expect, it } from 'vitest'
import { isHermesOptIn } from './opt-in'
it.each([['',false],['?ui=hermes',true],['?ui=other',false],['#?ui=hermes',false],['?x=1#?ui=hermes',false],['?ui=hermes#chat',true]])('opt-in %s', (input, expected) => expect(isHermesOptIn(input)).toBe(expected))
