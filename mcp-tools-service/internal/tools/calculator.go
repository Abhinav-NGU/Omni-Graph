package tools

import (
	"fmt"
	"strconv"
	"strings"
	"unicode"
)

// ExecuteCalculator evaluates a simple math expression.
// Uses a hand-rolled evaluator to avoid external dependencies.
// Supports: +, -, *, /, parentheses, decimals.
// Example input: "2 + 2 * (3 / 1.5)"
func ExecuteCalculator(expr string) (interface{}, error) {
	expr = strings.TrimSpace(expr)
	if expr == "" {
		return nil, fmt.Errorf("empty expression")
	}

	result, err := parseExpr(expr)
	if err != nil {
		return nil, fmt.Errorf("failed to evaluate '%s': %w", expr, err)
	}

	return map[string]interface{}{
		"expression": expr,
		"result":     result,
	}, nil
}

// ── Simple recursive descent parser ──────────────────────────────────────────

type parser struct {
	input []rune
	pos   int
}

func parseExpr(s string) (float64, error) {
	p := &parser{input: []rune(strings.ReplaceAll(s, " ", ""))}
	val, err := p.addSub()
	if err != nil {
		return 0, err
	}
	if p.pos != len(p.input) {
		return 0, fmt.Errorf("unexpected character at position %d", p.pos)
	}
	return val, nil
}

func (p *parser) addSub() (float64, error) {
	left, err := p.mulDiv()
	if err != nil {
		return 0, err
	}
	for p.pos < len(p.input) && (p.input[p.pos] == '+' || p.input[p.pos] == '-') {
		op := p.input[p.pos]
		p.pos++
		right, err := p.mulDiv()
		if err != nil {
			return 0, err
		}
		if op == '+' {
			left += right
		} else {
			left -= right
		}
	}
	return left, nil
}

func (p *parser) mulDiv() (float64, error) {
	left, err := p.unary()
	if err != nil {
		return 0, err
	}
	for p.pos < len(p.input) && (p.input[p.pos] == '*' || p.input[p.pos] == '/') {
		op := p.input[p.pos]
		p.pos++
		right, err := p.unary()
		if err != nil {
			return 0, err
		}
		if op == '*' {
			left *= right
		} else {
			if right == 0 {
				return 0, fmt.Errorf("division by zero")
			}
			left /= right
		}
	}
	return left, nil
}

func (p *parser) unary() (float64, error) {
	if p.pos < len(p.input) && p.input[p.pos] == '-' {
		p.pos++
		val, err := p.primary()
		return -val, err
	}
	return p.primary()
}

func (p *parser) primary() (float64, error) {
	if p.pos >= len(p.input) {
		return 0, fmt.Errorf("unexpected end of expression")
	}
	if p.input[p.pos] == '(' {
		p.pos++ // consume '('
		val, err := p.addSub()
		if err != nil {
			return 0, err
		}
		if p.pos >= len(p.input) || p.input[p.pos] != ')' {
			return 0, fmt.Errorf("missing closing parenthesis")
		}
		p.pos++ // consume ')'
		return val, nil
	}
	return p.number()
}

func (p *parser) number() (float64, error) {
	start := p.pos
	for p.pos < len(p.input) && (unicode.IsDigit(p.input[p.pos]) || p.input[p.pos] == '.') {
		p.pos++
	}
	if start == p.pos {
		return 0, fmt.Errorf("expected number at position %d, got '%c'", p.pos, p.input[p.pos])
	}
	return strconv.ParseFloat(string(p.input[start:p.pos]), 64)
}