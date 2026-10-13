// Проверка словарей: каждый ключ из исходников есть во всех языках, подстановки {0} совпадают, формы множественного числа — пары.
// Запуск: `node scripts/i18n-check.mjs`. Код возврата 1 при ошибках.
import fs from 'node:fs'
import path from 'node:path'
import { keys } from './i18n-keys.mjs'

const dir = path.resolve(import.meta.dirname, '../src/locales')
const LANGS = ['en', 'fr', 'de', 'es']
const holes = s => [...s.matchAll(/\{\d+\}/g)].map(m => m[0]).sort().join(',')
const source = keys()
const problems = []
const sizes = {}
for (const lang of LANGS) {
  const dict = JSON.parse(fs.readFileSync(path.join(dir, `${lang}.json`), 'utf8'))
  sizes[lang] = Object.keys(dict).length
  for (const key of source) {
    const value = dict[key]
    if (value === undefined) { problems.push(`${lang}: нет перевода «${key.slice(0, 70)}»`); continue }
    const plural = key.split('|').length === 3 && Array.isArray(value)
    if (Array.isArray(value)) {
      if (!plural || value.length !== 2 || value.some(v => typeof v !== 'string' || !v)) problems.push(`${lang}: формы множественного числа «${key}» — нужна пара строк`)
      continue
    }
    if (typeof value !== 'string' || !value.trim()) { problems.push(`${lang}: пустой перевод «${key.slice(0, 70)}»`); continue }
    if (holes(key) !== holes(value)) problems.push(`${lang}: не совпадают подстановки «${key.slice(0, 60)}» → «${value.slice(0, 60)}»`)
    if (value === key && /[А-Яа-яЁё]/.test(key) && !key.includes('\n')) problems.push(`${lang}: перевод совпадает с исходником «${key.slice(0, 60)}»`)
  }
  for (const [key, value] of Object.entries(dict)) {
    if (Array.isArray(value)) continue
    if (typeof value === 'string' && holes(key) !== holes(value)) problems.push(`${lang}: не совпадают подстановки в словаре «${key.slice(0, 60)}»`)
  }
}
if (new Set(Object.values(sizes)).size > 1) problems.push(`словари разной длины: ${JSON.stringify(sizes)}`)
if (problems.length) { console.error(problems.slice(0, 60).join('\n') + (problems.length > 60 ? `\n… и ещё ${problems.length - 60}` : '')); process.exit(1) }
console.log(`i18n: ${source.length} ключей из исходников, словарей ${LANGS.length}, строк в каждом: ${Object.values(sizes)[0]}`)
