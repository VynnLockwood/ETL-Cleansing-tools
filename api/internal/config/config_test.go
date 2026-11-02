package config

import "testing"

func clearEnv(t *testing.T) {
	t.Helper()
	for _, k := range []string{"DATABASE_URL", "MIGRATE_DATABASE_URL", "ADMIN_PASSWORD", "UPLOAD_DIR", "ADDR"} {
		t.Setenv(k, "")
	}
}

func TestLoadRequiresDatabaseURL(t *testing.T) {
	clearEnv(t)
	t.Setenv("ADMIN_PASSWORD", "x")
	if _, err := Load(); err == nil {
		t.Fatal("expected error when DATABASE_URL is empty")
	}
}

func TestLoadRequiresAdminPassword(t *testing.T) {
	clearEnv(t)
	t.Setenv("DATABASE_URL", "postgres://x")
	if _, err := Load(); err == nil {
		t.Fatal("expected error when ADMIN_PASSWORD is empty")
	}
}

func TestLoadDefaults(t *testing.T) {
	clearEnv(t)
	t.Setenv("DATABASE_URL", "postgres://x")
	t.Setenv("ADMIN_PASSWORD", "x")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.UploadDir != "/data/uploads" {
		t.Errorf("UploadDir = %q", cfg.UploadDir)
	}
	if cfg.Addr != ":8080" {
		t.Errorf("Addr = %q", cfg.Addr)
	}
}

func TestLoadMigrateURLDefaultsToDatabaseURL(t *testing.T) {
	clearEnv(t)
	t.Setenv("DATABASE_URL", "postgres://api")
	t.Setenv("ADMIN_PASSWORD", "x")
	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.MigrateDatabaseURL != "postgres://api" {
		t.Errorf("MigrateDatabaseURL = %q", cfg.MigrateDatabaseURL)
	}
	t.Setenv("MIGRATE_DATABASE_URL", "postgres://owner")
	cfg, err = Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.MigrateDatabaseURL != "postgres://owner" {
		t.Errorf("MigrateDatabaseURL = %q", cfg.MigrateDatabaseURL)
	}
}
