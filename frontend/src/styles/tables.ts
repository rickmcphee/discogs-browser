// Settings and Account lay their forms out as label / control / description
// tables. Below the breakpoint those three columns stack, which `display:
// block` on the rows and cells does with no change to the markup at all --
// and the markup is worth keeping. None of it is tabular *data*, but plenty
// of the test suite reaches a row through `closest('tr')`, and a rewrite to
// divs would churn all of that to say the same thing.
export const stackedTableClass = 'w-full text-sm border-collapse block md:table'
export const stackedBodyClass = 'block md:table-row-group'
export const stackedRowClass = 'block md:table-row'
export const stackedCellClass = 'block md:table-cell'
