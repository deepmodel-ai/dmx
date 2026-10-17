// Package ledger records balances in minor units.
package ledger

// Balance returns the sum of entries.
func Balance(entries []int64) int64 {
	var total int64
	for _, e := range entries {
		total += e
	}
	return total
}
