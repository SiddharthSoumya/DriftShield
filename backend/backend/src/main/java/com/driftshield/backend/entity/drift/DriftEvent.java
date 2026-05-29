package com.driftshield.backend.entity.drift;

import jakarta.persistence.*;
import lombok.*;

import java.time.LocalDateTime;

@Entity
@Table(name= "drift_events")
@Getter
@Setter
@NoArgsConstructor
@AllArgsConstructor
@Builder
public class DriftEvent {
    @Id@GeneratedValue(strategy=GenerationType.IDENTITY)
    private Long id;
    private String configKey;
    private String oldValue;
    private String newValue;
    private String severity;
    private boolean reverted;
    private LocalDateTime createdAt;
}
