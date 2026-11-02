package main

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/VynnLockwood/dmms-spp-database-design/api/internal/config"
)

func TestHealthz(t *testing.T) {
	h := newRouter(nil, config.Config{})
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, httptest.NewRequest(http.MethodGet, "/healthz", nil))
	if rec.Code != http.StatusOK {
		t.Fatalf("status = %d", rec.Code)
	}
	if got := strings.TrimSpace(rec.Body.String()); got != `{"status":"ok"}` {
		t.Fatalf("body = %q", got)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("content-type = %q", ct)
	}
}
