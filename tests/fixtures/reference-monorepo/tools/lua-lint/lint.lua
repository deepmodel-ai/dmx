-- Flags lines longer than 100 characters.
local M = {}

function M.too_long(line)
  return #line > 100
end

return M
