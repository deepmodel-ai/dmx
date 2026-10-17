package payments

import "testing"

func TestCanCharge(t *testing.T) {
	if !CanCharge([]int64{100}, 60) {
		t.Fatal("expected 100 to cover 60")
	}
}
