// musubi-tier: substrate
// expires-when: never - the desktop entry point is durable operator substrate
// Prevents a second console window on Windows in release.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    musubi_console_lib::run()
}
