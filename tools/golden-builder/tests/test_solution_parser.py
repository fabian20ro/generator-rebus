from app.parse.solution_parser import parse_solution_grid


def test_dash_black_square_until_row_full():
    grid, _ = parse_solution_grid("ABC-DEF")
    assert grid[0][3] == "#"


def test_letter_wraps_next_row():
    grid, _ = parse_solution_grid("ABCDEFGHIJK")
    assert grid[0][9] == "J"
    assert grid[1][0] == "K"
