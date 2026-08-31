package main

import (
	"fmt"
	"os"

	"github.com/HelloiOS2014/AgentRecovery/internal/cli"
)

// Version is set at build time via -ldflags.
var Version = "dev"

func main() {
	if len(os.Args) > 1 && (os.Args[1] == "--version" || os.Args[1] == "-v") {
		fmt.Println(Version)
		os.Exit(0)
	}
	os.Exit(cli.Main(os.Args[1:]))
}
