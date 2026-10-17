// Package payments charges against the ledger.
package payments

import "example.com/mono/services/ledger"

// CanCharge reports whether the balance covers amount.
func CanCharge(entries []int64, amount int64) bool {
	return ledger.Balance(entries) >= amount
}
