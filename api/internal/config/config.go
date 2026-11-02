// Package config loads runtime configuration from environment variables.
package config

import (
	"errors"
	"os"
)

type Config struct {
	DatabaseURL        string
	MigrateDatabaseURL string
	AdminPassword      string
	UploadDir          string
	Addr               string
}

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

// Load reads configuration from the environment.
func Load() (Config, error) {
	cfg := Config{
		DatabaseURL:   os.Getenv("DATABASE_URL"),
		AdminPassword: os.Getenv("ADMIN_PASSWORD"),
		UploadDir:     getenv("UPLOAD_DIR", "/data/uploads"),
		Addr:          getenv("ADDR", ":8080"),
	}
	if cfg.DatabaseURL == "" {
		return Config{}, errors.New("DATABASE_URL is required")
	}
	if cfg.AdminPassword == "" {
		return Config{}, errors.New("ADMIN_PASSWORD is required")
	}
	cfg.MigrateDatabaseURL = getenv("MIGRATE_DATABASE_URL", cfg.DatabaseURL)
	return cfg, nil
}
