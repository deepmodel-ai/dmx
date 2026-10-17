package ledger

import "testing"

func TestBalance(t *testing.T) {
	if got := Balance([]int64{100, -40}); got != 60 {
		t.Fatalf("Balance = %d, want 60", got)
	}
}
