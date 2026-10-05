# Load the repo's .env into the current fish shell (fish can't `source` a dotenv file).
#
#   source scripts/env.fish            # load ./.env of this repo
#   source scripts/env.fish --force    # let .env override variables already set in the shell
#   source scripts/env.fish path/to/other.env
#
# Same rules as scripts/_lib.sh and docker compose: comments and blank lines are skipped,
# `export KEY=...` is accepted, 'single-quoted' values are literal, "double-quoted" values
# understand \" \\ \n, unquoted values end at an inline ` #` comment. Variables already set
# in the shell win over .env unless --force is given. Values are never printed.
# Requires fish >= 3.4.

function __llmll_load_env
    set -l force 0
    set -l file
    for arg in $argv
        switch $arg
            case --force -f
                set force 1
            case '*'
                set file $arg
        end
    end
    if test -z "$file"
        set file (path resolve (path dirname (status filename))/../.env)
    end
    if not test -f "$file"
        echo "env.fish: $file not found (copy .env.example to .env first)" >&2
        return 1
    end

    set -l loaded 0
    set -l kept
    set -l bad
    set -l lineno 0
    while read -l line
        set lineno (math $lineno + 1)
        set line (string trim -- (string replace -r '\r$' '' -- $line))
        if test -z "$line"; or string match -q '#*' -- $line
            continue
        end
        set line (string replace -r '^export\s+' '' -- $line)
        set -l parts (string match -rg '^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' -- $line)
        if test (count $parts) -lt 1
            set -a bad $lineno
            continue
        end
        set -l name $parts[1]
        set -l raw ''
        test (count $parts) -ge 2; and set raw $parts[2]

        set -l value
        if string match -q "'*" -- $raw
            set value (string match -rg "^'([^']*)'" -- $raw)
            or begin
                set -a bad $lineno
                continue
            end
        else if string match -q '"*' -- $raw
            set value (string match -rg '^"((?:[^"\\\\]|\\\\.)*)"' -- $raw)
            or begin
                set -a bad $lineno
                continue
            end
            set value (string replace -a '\\n' \n -- $value | string replace -a '\\"' '"' | string replace -a '\\\\' '\\' | string collect)
        else
            set value (string trim -- (string replace -r '\s+#.*$' '' -- $raw))
        end

        if test $force -eq 0; and set -q $name; and test -n "$$name"
            set -a kept $name
            continue
        end
        set -gx $name $value
        set loaded (math $loaded + 1)
    end <$file

    set -l msg "env.fish: loaded $loaded variable(s) from $file"
    if test (count $kept) -gt 0
        set msg "$msg; kept from the shell: "(string join ', ' $kept)" (use --force to override)"
    end
    echo $msg
    if test (count $bad) -gt 0
        echo "env.fish: could not parse line(s) "(string join ', ' $bad) >&2
        return 1
    end
end

__llmll_load_env $argv
set -l __llmll_status $status
functions -e __llmll_load_env
return $__llmll_status
