//! Stable content hashes.

/// FNV-1a over the input bytes.
pub fn fnv1a(data: &[u8]) -> u64 {
    data.iter().fold(0xcbf29ce484222325, |hash, byte| {
        (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3)
    })
}

#[cfg(test)]
mod tests {
    #[test]
    fn empty_input_is_the_offset_basis() {
        assert_eq!(super::fnv1a(b""), 0xcbf29ce484222325);
    }
}
