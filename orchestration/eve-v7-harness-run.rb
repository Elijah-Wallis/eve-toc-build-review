#!/usr/bin/env ruby
require 'yaml'

orch_file = ARGV[0] || 'orchestration/eve-v7-orchestrator.yaml'
test_file = ARGV[1] || 'orchestration/eve-v7-test-cases.yaml'
orch = YAML.load_file(orch_file)
tests = YAML.load_file(test_file)

states = orch.dig('flow', 'states')
start = orch.dig('flow', 'start')
contracts = orch['contracts'].map { |c| c['name'] }
regex_rules = {}
orch.dig('parser', 'regex')&.each do |name, pattern|
  begin
    regex_rules[name.to_sym] = Regexp.new(pattern, Regexp::IGNORECASE)
  rescue RegexpError => e
    puts "Invalid regex #{name}: #{e.message}"
    exit 1
  end
end

DNC_PATTERN = /\b(do not call|dont call|stop calls|remove from list|delete my number|unsubscribe|opt out|off the list)\b/i

def infer_intents(text, regex_rules)
  intents = {}
  intents[:hostile] = !!(text =~ /hang up|you idiot|f\*+ck|damn|shut up|absolute dumpster fire|trash/i)
  intents[:dnc] = !!(text =~ DNC_PATTERN)
  intents[:answering_service] = !!(text =~ /answering service|call center|virtual assistant|\bva\b/i)
  intents[:is_sales] = !!(text =~ /are you a sales call|this is a sales|marketing agency|sales call/i)
  intents[:info_email] = !!(text =~ /info@|front desk|frontdesk|generic inbox/i)
  intents[:ai_disclosure] = !!(text =~ /are you a robot|are you ai|are you a bot|is this a bot|is this ai/i)
  intents[:skeptical] = !!(text =~ /not interested|not buying|not going to send|sounds like spam/i)
  intents[:wants_sms] = !!(text =~ /text|sms|message/i)
  intents[:yes] = !!(text =~ /\byes\b|\byeah\b|\byep\b/i)
  intents[:no] = !!(text =~ /\bno\b|\bnah\b|\bnope\b/i)
  intents[:accept_send] = !!(text =~ /send|okay|sure|yes|yeah/i)
  intents[:email] = !!(text =~ regex_rules[:email]) if regex_rules[:email]
  intents[:user_provides_direct_email] = intents[:email]
  intents
end

def evaluate_transition(state_def, intents, user_text)
  transitions = state_def['transitions'] || []
  fallback = nil
  transitions.each do |t|
    cond = t['when']
    case cond
    when 'sentiment == hostile'
      return t['goto'] if intents[:hostile]
    when 'user_intent == ai_disclosure'
      return t['goto'] if intents[:ai_disclosure]
    when 'user_intent == dnc'
      return t['goto'] if intents[:dnc]
    when 'user_intent in [skeptical, is_sales]'
      return t['goto'] if intents[:skeptical] || intents[:is_sales]
    when 'user_intent == answering_service'
      return t['goto'] if intents[:answering_service]
    when 'user_intent == info_email'
      return t['goto'] if intents[:info_email]
    when 'user_intent == user_accepts_send'
      return t['goto'] if intents[:accept_send]
    when 'user_intent == user_wants_sms'
      return t['goto'] if intents[:wants_sms]
    when 'user_intent == user_provides_direct_email'
      return t['goto'] if intents[:user_provides_direct_email]
    when 'user_reply in [yes, true, admits_pain]'
      return t['goto'] if intents[:yes] && !intents[:no]
    when 'user_reply in [no, denies, not_like_that]'
      return t['goto'] if intents[:no] || intents[:skeptical]
    when true, 'true'
      fallback = t['goto']
    end
  end
  fallback
end

def advance_goto_chain(state, states, transitions, path, tools_seen)
  loop do
    state_def = states[state]
    break unless state_def

    tool_name = state_def['tool']
    tools_seen << tool_name if tool_name

    goto_state = state_def['goto']
    break unless goto_state

    transitions << [state, goto_state]
    state = goto_state
    path << state
  end
  state
end

cases = tests['tests'] || []
pass = 0

puts "Harness contract: #{orch_file}"
puts "Test set:    #{test_file}"
puts "States:      #{states.keys.size}, start=#{start}"
puts "Contracts:   #{contracts.join(', ')}"
puts

cases.each do |tc|
  state = tc['expected_start_state'] || start
  path = [state]
  transitions = []
  tools_seen = []
  ok = true

    (tc['turns'] || []).each do |turn|
      if turn['user']
        intents = infer_intents(turn['user'], regex_rules)
        next_state = evaluate_transition(states[state], intents, turn['user'])
        if next_state.nil?
          next_state = state
        end
        transitions << [state, next_state]
        state = next_state
        state = advance_goto_chain(state, states, transitions, path, tools_seen)
      elsif turn['assistant_state']
        expected_state = turn['assistant_state']
        if state != expected_state
          transitions << [state, expected_state]
          path << expected_state if path.last != expected_state
        end
        state = expected_state
        state = advance_goto_chain(state, states, transitions, path, tools_seen)
        if states[state].nil?
          ok = false
          puts "  invalid_assistant_state=#{state} in #{tc['id']}"
        end
      end
    end

  if tc['expected_transitions']
    tc['expected_transitions'].each do |tr|
      found = transitions.any? { |s, t| s == tr['from'] && t == tr['to'] }
      ok = false unless found
    end
  end

  if tc['expected_tool_calls']
    tc['expected_tool_calls'].each do |call|
      call.keys.each do |tool|
        found = tools_seen.include?(tool)
        ok = false unless found
      end
    end
  end

  if tc['expected_final_state']
    ok = false unless state == tc['expected_final_state']
  end

  pass += 1 if ok
  puts "#{tc['id']} #{ok ? 'PASS' : 'FAIL'}"
  puts "  path: #{path.join(' -> ')}"
  if tc['expected_transitions']
    tc['expected_transitions'].each do |tr|
      found = transitions.any? { |s, t| s == tr['from'] && t == tr['to'] }
      status = found ? 'OK' : 'MISSING'
      puts "  transition #{tr['from']} -> #{tr['to']} #{status}"
    end
  end
  if tc['expected_tool_calls']
    tc['expected_tool_calls'].each do |call|
      call.keys.each do |tool|
        found = tools_seen.include?(tool)
        status = found ? 'OK' : 'MISSING'
        puts "  tool #{tool} #{status}"
      end
    end
  end
  puts
end

puts "Summary: #{pass}/#{cases.size} test groups passing"
