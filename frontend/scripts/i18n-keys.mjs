// Список ключей перевода из исходников: первые аргументы tr('…') и тройки plural(n, '…', '…', '…'). Вывод — JSON-массив строк.
import ts from 'typescript'
import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '../src')
function* walk(dir) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name)
    if (e.isDirectory()) yield* walk(p)
    else if (/\.(ts|tsx)$/.test(e.name) && !/locales\//.test(p)) yield p
  }
}
export function keys() {
  const out = new Set()
  for (const file of walk(root)) {
    const sf = ts.createSourceFile(file, fs.readFileSync(file, 'utf8'), ts.ScriptTarget.Latest, true, file.endsWith('x') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
    const lit = n => (n && (ts.isStringLiteral(n) || ts.isNoSubstitutionTemplateLiteral(n)) ? n.text : null)
    const visit = n => {
      if (ts.isCallExpression(n) && ts.isIdentifier(n.expression)) {
        const name = n.expression.text
        if (name === 'tr') { const k = lit(n.arguments[0]); if (k !== null) out.add(k) }
        if (name === 'plural' && n.arguments.length === 4) {
          const f = [1, 2, 3].map(i => lit(n.arguments[i]))
          if (f.every(x => x !== null)) out.add(f.join('|'))
        }
      }
      ts.forEachChild(n, visit)
    }
    visit(sf)
  }
  return [...out].sort()
}
if (process.argv[1] === import.meta.filename) console.log(JSON.stringify(keys(), null, 0))
