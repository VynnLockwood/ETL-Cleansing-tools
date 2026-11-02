package db

import (
	"context"
	"os"
	"testing"
)

func TestMigrateCreatesSchemas(t *testing.T) {
	url := os.Getenv("TEST_DATABASE_URL")
	if url == "" {
		t.Skip("TEST_DATABASE_URL not set")
	}
	// Run twice: the second run must be a no-op, not an error.
	for i := 0; i < 2; i++ {
		if err := Migrate(url); err != nil {
			t.Fatalf("Migrate run %d: %v", i+1, err)
		}
	}
	ctx := context.Background()
	pool, err := Open(ctx, url)
	if err != nil {
		t.Fatal(err)
	}
	defer pool.Close()
	for _, s := range []string{"spp", "app", "usecase"} {
		var n int
		err := pool.QueryRow(ctx, `SELECT count(*) FROM information_schema.schemata WHERE schema_name = $1`, s).Scan(&n)
		if err != nil || n != 1 {
			t.Errorf("schema %s: n=%d err=%v", s, n, err)
		}
	}
}
