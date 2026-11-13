import { useRef } from 'react';
import { Pressable, ScrollView, StyleSheet, Text } from 'react-native';
import { theme } from '../theme';

export function ChoiceRow<T extends string>({ options, value, onChange }: {
  options: { value: T; label: string; disabled?: boolean }[];
  value: T | undefined;
  onChange: (v: T) => void;
}) {
  const scroll = useRef<ScrollView>(null);
  const revealed = useRef(false);

  // On first layout, bring the selected chip into view (e.g. a restaurant
  // opened from an offer that sits far to the right of the row).
  const reveal = (x: number) => {
    if (revealed.current) return;
    revealed.current = true;
    if (x > 0) scroll.current?.scrollTo({ x: Math.max(0, x - 16), animated: false });
  };

  return (
    <ScrollView ref={scroll} horizontal style={{ flexGrow: 0 }} showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 8, paddingVertical: 4 }}>
      {options.map((o) => (
        <Pressable
          key={o.value}
          disabled={o.disabled}
          onLayout={o.value === value ? (e) => reveal(e.nativeEvent.layout.x) : undefined}
          onPress={() => onChange(o.value)}
          style={[styles.chip, value === o.value && styles.on, o.disabled && { opacity: 0.35 }]}
        >
          <Text style={[styles.text, value === o.value && { color: theme.color.onDark }]}>{o.label}</Text>
        </Pressable>
      ))}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  chip: { paddingHorizontal: 14, paddingVertical: 9, borderRadius: 999, borderWidth: 1, borderColor: theme.color.border, backgroundColor: theme.color.surface },
  on: { backgroundColor: theme.color.red, borderColor: theme.color.red },
  text: { fontWeight: '700', color: theme.color.muted },
});
